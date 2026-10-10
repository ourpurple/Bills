package dev.bills.app

import java.math.BigDecimal
import java.net.URI
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.UUID

object Rules {
    val zone: ZoneId = ZoneId.of("Asia/Shanghai")
    val dateTime: DateTimeFormatter =
        DateTimeFormatter.ofPattern("uuuu-MM-dd HH:mm")
            .withResolverStyle(java.time.format.ResolverStyle.STRICT)

    fun server(raw: String, debug: Boolean): String {
        val text = raw.trim()
        require(text.isNotEmpty() && text.none { it.isWhitespace() || it.code < 32 }) {
            "请输入完整的 HTTPS 服务地址"
        }
        val uri =
            try {
                URI(text)
            } catch (_: Exception) {
                throw IllegalArgumentException("服务地址格式无效")
            }
        require(
            uri.host != null &&
                uri.rawUserInfo == null &&
                uri.rawQuery == null &&
                uri.rawFragment == null &&
                (uri.rawPath.isNullOrEmpty() || uri.rawPath == "/")
        ) {
            "只填写服务根地址，不含路径、账号或参数"
        }
        require(uri.port == -1 || uri.port in 1..65535) { "端口无效" }
        val local = uri.host.lowercase() in setOf("10.0.2.2", "127.0.0.1", "localhost")
        require(uri.scheme == "https" || (debug && uri.scheme == "http" && local)) {
            "必须使用 HTTPS；调试版仅允许本机测试地址使用 HTTP"
        }
        return URI(uri.scheme, null, uri.host.lowercase(), uri.port, null, null, null).toString()
    }

    fun amount(raw: String, currency: String): String {
        require(currency in setOf("CNY", "USD", "EUR", "HKD", "JPY")) { "币种不支持" }
        require(Regex("[0-9]{1,12}(\\.[0-9]{1,2})?").matches(raw.trim())) { "金额须为正数，最多两位小数" }
        val money = BigDecimal(raw.trim())
        require(money > BigDecimal.ZERO && money <= BigDecimal("999999999999.99")) {
            "金额须大于零且不能超过范围"
        }
        val scale = if (currency == "JPY") 0 else 2
        try {
            return money.setScale(scale).toPlainString()
        } catch (_: ArithmeticException) {
            throw IllegalArgumentException("日元金额不允许小数")
        }
    }

    fun instant(raw: String): String =
        try {
            LocalDateTime.parse(raw.trim(), dateTime).atZone(zone).toOffsetDateTime().toString()
        } catch (_: Exception) {
            throw IllegalArgumentException("时间格式应为 yyyy-MM-dd HH:mm，按北京时间填写")
        }

    fun expired(expiresAt: String, now: java.time.Instant = java.time.Instant.now()): Boolean =
        try {
            !java.time.OffsetDateTime.parse(expiresAt).toInstant().isAfter(now)
        } catch (_: Exception) {
            true
        }

    fun key(): String = UUID.randomUUID().toString()
}

data class Session(
    val server: String,
    val token: String,
    val id: String,
    val name: String,
    val expiresAt: String,
    val scope: String = Rules.key(),
)

data class Connection(val server: String, val name: String, val expiresAt: String)

data class Account(
    val id: String,
    val name: String,
    val bankName: String,
    val holderName: String,
    val mode: String,
    val active: Boolean,
    val tails: List<String>,
)

data class Statement(
    val id: String,
    val account: Account,
    val date: String,
    val currency: String,
    val status: String,
    val version: Int,
    val amount: String?,
    val minimum: String?,
    val due: String?,
    val paid: String,
    val remaining: String?,
    val paymentStatus: String,
    val days: Int?,
    val overdue: Boolean,
    val transactionStatus: String,
    val periodStart: String?,
    val periodEnd: String?,
    val source: String,
)

data class Total(val currency: String, val amount: String)

data class Warning(val accountId: String, val accountName: String, val message: String)

data class Overview(
    val totals: List<Total>,
    val latest: List<Statement>,
    val historical: List<Statement>,
    val warnings: List<Warning>,
    val pending: Int,
    val disclaimer: String,
    val asOf: String,
)

data class Transaction(
    val description: String,
    val date: String?,
    val posted: String?,
    val amount: String?,
    val currency: String?,
    val direction: String,
    val tail: String?,
)

data class PaymentRecord(
    val id: String,
    val amount: String,
    val currency: String,
    val time: String,
    val note: String,
    val endedAt: String?,
    val reason: String?,
)

data class Page<T>(
    val items: List<T>,
    val total: Int,
    val page: Int,
    val size: Int,
    val version: Int? = null,
    val detailStatus: String? = null,
) {
    val hasNext: Boolean
        get() = page * size < total
}

data class Loaded<T>(val data: T, val at: Long, val cached: Boolean)

// A persisted uncertain write is retried only by an explicit user action, never a worker.
data class Pending(
    val scope: String,
    val key: String,
    val path: String,
    val body: String,
    val title: String,
    val needsConfirmation: Boolean = false,
    val uncertain: Boolean = false,
)

class ApiError(val status: Int, val code: String, override val message: String) :
    Exception(message)

fun money(value: String?, currency: String): String =
    if (value == null) "未知" else "$currency $value"

fun status(value: String): String =
    when (value) {
        "confirmed" -> "已确认"
        "draft" -> "待后台确认"
        "void" -> "已作废"
        "settled" -> "已标记结清"
        "overpaid" -> "已标记超额"
        "partial" -> "已标记部分还款"
        "unpaid" -> "尚未标记还款"
        else -> "金额未知"
    }

fun detailStatus(value: String): String =
    when (value) {
        "complete" -> "已提取详单（仍以银行为准）"
        "partial" -> "详单不完整，缺失部分请到后台核对"
        else -> "未提供或未获得详单，不等于无交易"
    }

fun canForgetRejection(status: Int, wasUncertain: Boolean): Boolean =
    !wasUncertain && status in setOf(400, 403, 404, 409, 422, 429)

fun validReceiptId(value: Any?): Boolean = value is String && value.matches(Regex("[0-9a-f]{32}"))
