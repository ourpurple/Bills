package dev.bills.app

import android.os.SystemClock
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

open class Api {
    open suspend fun call(
        server: String,
        path: String,
        token: String? = null,
        body: String? = null,
        key: String? = null,
    ): String =
        withContext(Dispatchers.IO) {
            require(
                path.startsWith("/api/v1/app/") || (path == "/api/v1/device/pair" && token == null)
            )
            val conn = URL(server + path).openConnection() as HttpURLConnection
            try {
                conn.instanceFollowRedirects = false
                conn.connectTimeout = 10000
                conn.readTimeout = 15000
                conn.useCaches = false
                conn.requestMethod = if (body == null) "GET" else "POST"
                conn.setRequestProperty("Accept", "application/json")
                if (token != null) conn.setRequestProperty("Authorization", "Bearer $token")
                if (key != null) conn.setRequestProperty("Idempotency-Key", key)
                val deadline = SystemClock.elapsedRealtime() + 25000
                if (body != null) {
                    conn.doOutput = true
                    conn.setRequestProperty("Content-Type", "application/json")
                    val bytes = body.toByteArray(Charsets.UTF_8)
                    conn.setFixedLengthStreamingMode(bytes.size)
                    conn.outputStream.use { it.write(bytes) }
                }
                val status = conn.responseCode
                val stream = if (status in 200..299) conn.inputStream else conn.errorStream
                val out = ByteArrayOutputStream()
                stream?.use { input ->
                    val buffer = ByteArray(8192)
                    while (true) {
                        if (SystemClock.elapsedRealtime() > deadline) throw IOException("timeout")
                        val n = input.read(buffer)
                        if (n < 0) break
                        if (out.size() + n > 4 * 1024 * 1024) throw IOException("response limit")
                        out.write(buffer, 0, n)
                    }
                }
                val text = out.toString("UTF-8")
                if (status !in 200..299) {
                    val code =
                        try {
                            JSONObject(text).getJSONObject("error").getString("code")
                        } catch (_: Exception) {
                            "HTTP_ERROR"
                        }
                    // Fixed local messages: never display a proxy HTML page or echo secret-bearing
                    // input.
                    val message =
                        when {
                            status == 401 -> "设备凭证已失效，请重新配对"
                            code == "CONFIRM_EXCEPTION" -> "金额超额或时间早于出账，请再次核对"
                            code == "IDEMPOTENCY_CONFLICT" -> "提交内容冲突，请在后台核对记录"
                            status == 429 -> "请求过于频繁，请稍后重试"
                            status == 422 -> "字段或账单状态不允许此操作，请检查金额、时间或到后台核对"
                            status in 300..399 -> "服务地址发生跳转，请填写最终 HTTPS 地址"
                            status == 403 -> "设备无权执行此操作"
                            else -> "服务暂不可用（$status），请重试或到后台核对"
                        }
                    throw ApiError(status, code, message)
                }
                text
            } finally {
                conn.disconnect()
            }
        }
}

class Repository(private val vault: Vault, private val db: CacheDb, private val api: Api = Api()) {
    var session: Session? = null
        private set

    var pending: Pending? = null
        private set

    suspend fun restore(): Boolean =
        withContext(Dispatchers.IO) {
            try {
                session = vault.read("session")?.let(Json::session)
                session?.let {
                    require(
                        Rules.server(it.server, BuildConfig.DEBUG) == it.server &&
                            !Rules.expired(it.expiresAt)
                    )
                }
                pending =
                    vault.read("pending")?.let(Json::pending)?.takeIf { it.scope == session?.scope }
                if (session == null) {
                    db.cache().clear()
                    vault.remove("pending")
                }
                false
            } catch (_: Exception) {
                clear()
                true
            }
        }

    suspend fun pair(server: String, code: String, name: String) {
        check(session == null)
        require(code.length in 16..128 && name.length in 1..80) { "请填写有效配对码和设备名称" }
        val o =
            JSONObject(
                api.call(
                    server,
                    "/api/v1/device/pair",
                    body = JSONObject().put("code", code).put("name", name).toString(),
                )
            )
        val s =
            Session(
                server,
                o.getString("token"),
                o.getString("id"),
                name,
                o.getString("expires_at"),
            )
        withContext(Dispatchers.IO) {
            db.cache().clear()
            vault.write("session", Json.session(s))
        }
        session = s
    }

    suspend fun clear() =
        withContext(Dispatchers.IO) {
            session = null
            pending = null
            vault.destroy()
            db.cache().clear()
        }

    suspend fun disconnect(): Boolean {
        val s = session ?: return true
        var revoked = false
        try {
            api.call(s.server, "/api/v1/app/device/disconnect", s.token, "{}")
            revoked = true
        } catch (_: Exception) {} finally {
            clear()
        }
        return revoked
    }

    private suspend fun request(path: String, body: String? = null, key: String? = null): String {
        val s = session ?: throw ApiError(401, "UNAUTHENTICATED", "请先配对")
        if (Rules.expired(s.expiresAt)) {
            clear()
            throw ApiError(401, "UNAUTHENTICATED", "设备凭证已到期，请重新配对")
        }
        try {
            return api.call(s.server, path, s.token, body, key)
        } catch (e: ApiError) {
            if (e.status == 401) clear()
            throw e
        }
    }

    suspend fun <T> read(path: String, decode: (String) -> T): Loaded<T> {
        val s = session ?: throw ApiError(401, "UNAUTHENTICATED", "请先配对")
        val key = s.scope + path
        try {
            val text = request(path)
            val value = decode(text)
            val now = System.currentTimeMillis()
            withContext(Dispatchers.IO) {
                db.cache().put(CacheRow(key, vault.encrypt(text), now))
                db.cache().prune()
            }
            return Loaded(value, now, false)
        } catch (e: Exception) {
            if (e is ApiError && e.status < 500) throw e
            val row = withContext(Dispatchers.IO) { db.cache().get(key) }
            if (row != null && System.currentTimeMillis() - row.at in 0..30L * 24 * 3600 * 1000) {
                try {
                    return Loaded(
                        decode(withContext(Dispatchers.IO) { vault.decrypt(row.payload) }),
                        row.at,
                        true,
                    )
                } catch (_: Exception) {}
            }
            throw e
        }
    }

    suspend fun stage(path: String, body: String, title: String) {
        check(pending == null) { "先核对上次尚未确认的提交" }
        val p = Pending(session?.scope ?: error("请先配对"), Rules.key(), path, body, title)
        withContext(Dispatchers.IO) { vault.write("pending", Json.pending(p)) }
        pending = p
    }

    suspend fun confirm() {
        val p = pending ?: return
        check(p.needsConfirmation)
        val next =
            p.copy(
                body = JSONObject(p.body).put("confirm_exception", true).toString(),
                needsConfirmation = false,
                uncertain = false,
            )
        withContext(Dispatchers.IO) { vault.write("pending", Json.pending(next)) }
        pending = next
    }

    suspend fun abandonRejected() {
        check(pending?.needsConfirmation == true)
        withContext(Dispatchers.IO) { vault.remove("pending") }
        pending = null
    }

    suspend fun submit() {
        val p = pending ?: return
        check(p.scope == session?.scope && !p.needsConfirmation)
        val started = p.copy(uncertain = true)
        withContext(Dispatchers.IO) { vault.write("pending", Json.pending(started)) }
        pending = started
        try {
            val response = request(p.path, p.body, p.key)
            // Validate receipt before declaring success. An unreadable 2xx is uncertain, not a
            // failure receipt.
            val receipt = JSONObject(response)
            require(validReceiptId(receipt.opt("id")))
            withContext(Dispatchers.IO) {
                vault.remove("pending")
                db.cache().clear()
            }
            pending = null
        } catch (e: ApiError) {
            if (e.code == "CONFIRM_EXCEPTION") {
                val next = p.copy(needsConfirmation = true, uncertain = false)
                withContext(Dispatchers.IO) { vault.write("pending", Json.pending(next)) }
                pending = next
            } else if (canForgetRejection(e.status, p.uncertain)) {
                withContext(Dispatchers.IO) { vault.remove("pending") }
                pending = null
            }
            throw e
        }
    }
}
