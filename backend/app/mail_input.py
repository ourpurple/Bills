"""Generic MIME/document decoding only. No bank, amount or transaction extraction.

Untrusted documents are converted in a bounded subprocess, never in API process.
HTML is reduced to plain text; links/images/scripts/forms are never executed.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

from .core import DomainError
from .mail_storage import blob_path

CONVERTER_VERSION = "generic-v1"


def metadata(data: bytes):
    from datetime import timezone
    from email.utils import parsedate_to_datetime

    msg = BytesParser(policy=policy.default).parsebytes(data, headersonly=True)

    def header(name, limit=500):
        try:
            return str(msg.get(name, ""))[:limit]
        except (ValueError, LookupError):
            return "[邮件头无法解码]"

    sent = None
    try:
        dt = parsedate_to_datetime(header("Date"))
        if dt.tzinfo is not None:
            sent = dt.astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        pass
    return dict(
        subject=header("Subject"), sender=header("From"), message_id=header("Message-ID"), sent_at=sent
    )


def canonical(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def convert_bytes(data: bytes, media_type: str, limits: dict) -> dict:
    warnings: list[str] = []
    parts: list[dict] = []
    remaining = limits["chars"]

    def add(name, kind, text, digest):
        nonlocal remaining
        text = canonical(text)
        if not text:
            warnings.append("NO_TEXT")
            return
        if len(text) > remaining:
            text = text[:remaining]
            warnings.append("TEXT_TRUNCATED")
        if text:
            parts.append(dict(name=name[:180], media_type=kind, text=text, sha256=digest))
        remaining -= len(text)

    def pdf(payload, name):
        if len(payload) > limits["attachment"]:
            warnings.append("ATTACHMENT_TOO_LARGE")
            return
        try:
            reader = PdfReader(io.BytesIO(payload), strict=False)
            if reader.is_encrypted:
                warnings.append("PDF_ENCRYPTED")
                return
            if len(reader.pages) > limits["pages"]:
                warnings.append("PDF_PAGE_LIMIT")
                return
            text = []
            total = 0
            for page in reader.pages:
                # Cap the uncompressed content stream before the expensive text walk.
                stream = page.get_contents()
                if stream is not None and len(stream.get_data()) > 8 * 1024 * 1024:
                    warnings.append("PDF_STREAM_LIMIT")
                    return
                value = page.extract_text() or ""
                text.append(value[: max(remaining - total, 0)])
                total += len(value)
                if total > remaining:
                    warnings.append("TEXT_TRUNCATED")
                    break
            add(name, "application/pdf", "\n\n".join(text), hashlib.sha256(payload).hexdigest())
        except Exception:
            # No raw parser exception (may contain private strings) is exposed.
            warnings.append("PDF_INVALID_OR_RESOURCE_LIMIT")

    def decoded(part, payload):
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="strict")
        except (UnicodeError, LookupError):
            warnings.append("TEXT_DECODE_FAILED")
            return ""

    if media_type == "application/pdf":
        pdf(data, "上传 PDF")
    else:
        try:
            message = BytesParser(policy=policy.default).parsebytes(data)
            if not any(message.get(h) for h in ("From", "Subject", "MIME-Version", "Content-Type")):
                warnings.append("EML_INVALID")
            else:
                count = 0

                def walk(part):
                    nonlocal count
                    count += 1
                    if count > 120:
                        warnings.append("MIME_PART_LIMIT")
                        return
                    if part.defects:
                        warnings.append("MIME_DEFECT")
                    if part.get_content_type() == "message/rfc822":
                        warnings.append("NESTED_MESSAGE_UNSUPPORTED")
                        return
                    if part.is_multipart():
                        children = list(part.iter_parts())
                        if part.get_content_subtype() == "alternative":
                            # Prefer one representation, avoiding doubled body text.
                            chosen = next((p for p in children if p.get_content_type() == "text/plain"), None)
                            if chosen is None:
                                chosen = next(
                                    (p for p in children if p.get_content_type() == "text/html"), None
                                )
                            if chosen is not None:
                                walk(chosen)
                            else:
                                for child in children:
                                    walk(child)
                        else:
                            for child in children:
                                walk(child)
                        return
                    payload = part.get_payload(decode=True) or b""
                    name = part.get_filename() or "邮件正文"
                    kind = part.get_content_type()
                    attached = (
                        part.get_content_disposition() == "attachment" or part.get_filename() is not None
                    )
                    if attached and len(payload) > limits["attachment"]:
                        warnings.append("ATTACHMENT_TOO_LARGE")
                    elif kind == "application/pdf" or name.lower().endswith(".pdf"):
                        pdf(payload, name)
                    elif kind in {"text/plain", "text/html"}:
                        text = decoded(part, payload)
                        if kind == "text/html":
                            soup = BeautifulSoup(text, "html.parser")
                            for tag in soup(
                                [
                                    "script",
                                    "style",
                                    "iframe",
                                    "object",
                                    "form",
                                    "svg",
                                    "img",
                                    "link",
                                    "meta",
                                    "noscript",
                                ]
                            ):
                                tag.decompose()
                            text = soup.get_text(separator="\n", strip=True)
                        add(name, kind, text, hashlib.sha256(canonical(text).encode()).hexdigest())
                    else:
                        warnings.append("ATTACHMENT_UNSUPPORTED" if attached else "PART_UNSUPPORTED")

                walk(message)
        except Exception:
            warnings.append("EML_INVALID_OR_RESOURCE_LIMIT")
    # Stable artifact bytes must match the filename-independent content fingerprint,
    # even if a previous worker wrote the file then crashed before DB commit.
    for index, part in enumerate(parts, 1):
        part["name"] = f"资料片段 {index}"
    warnings = sorted(set(warnings))
    state = "unsupported" if not parts else "partial" if warnings else "ready"
    # Fingerprint excludes routing headers and filenames; preserves content/attachments.
    key_payload = [[p["media_type"], p["text"], p["sha256"]] for p in parts]
    if not parts:
        key_payload = [[media_type, hashlib.sha256(data).hexdigest()]]
    key = hashlib.sha256(
        json.dumps(
            [CONVERTER_VERSION, key_payload, warnings], ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return dict(
        version=CONVERTER_VERSION,
        id=key,
        state=state,
        parts=parts,
        warnings=warnings,
        text_chars=sum(len(p["text"]) for p in parts),
    )


def prepare_isolated(settings, raw_hash: str, media_type: str) -> dict:
    source = blob_path(settings, raw_hash)
    if not source.is_file():
        raise DomainError("SOURCE_UNAVAILABLE", "原始资料不存在", 404)
    limits = dict(
        chars=settings.input_max_chars, pages=settings.pdf_max_pages, attachment=settings.attachment_max_bytes
    )
    with tempfile.TemporaryDirectory(prefix="bills-input-") as folder:
        output = Path(folder) / "output.json"
        try:
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.mail_input",
                    str(source),
                    str(output),
                    media_type,
                    json.dumps(limits),
                ],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=settings.input_timeout_seconds,
            )
        except subprocess.TimeoutExpired as e:
            raise DomainError("INPUT_TIMEOUT", "资料转换超时，可检查附件后重试", 422) from e
        if run.returncode != 0 or not output.is_file():
            raise DomainError("INPUT_CONVERSION_FAILED", "资料转换失败或超过资源限制", 422)
        # JSON escapes can occupy six bytes per input character. Bound parent reads too.
        if output.stat().st_size > settings.input_max_chars * 8 + 512_000:
            raise DomainError("INPUT_OUTPUT_LIMIT", "资料转换输出超过上限", 422)
        return json.loads(output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    raw = Path(sys.argv[1]).read_bytes()
    result = convert_bytes(raw, sys.argv[3], json.loads(sys.argv[4]))
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
