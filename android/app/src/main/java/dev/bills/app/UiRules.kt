package dev.bills.app

import java.math.BigDecimal
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.time.LocalDate
import java.util.Locale

internal data class DueLabel(val value: String, val caption: String, val urgent: Boolean)

internal fun dueLabel(row: Statement): DueLabel =
    when {
        row.status == "void" -> DueLabel("—", "已作废", false)
        row.paymentStatus == "settled" -> DueLabel("✓", "已标记还清", false)
        row.paymentStatus == "overpaid" -> DueLabel("✓", "已标记超额", false)
        row.due == null || row.days == null -> DueLabel("—", "到期日未知", false)
        row.overdue || row.days < 0 ->
            DueLabel(kotlin.math.abs(row.days.toLong()).toString(), "天已逾期", true)
        row.days == 0 -> DueLabel("今", "天到期", true)
        row.days == 1 -> DueLabel("明", "天到期", true)
        else -> DueLabel(row.days.toString(), "天后到期", row.days <= 3)
    }

internal fun displayAmount(value: String?, currency: String, hidden: Boolean = false): String {
    if (hidden) return "••••"
    if (value == null) return "未知"
    return try {
        val pattern = if (currency == "JPY") "#,##0" else "#,##0.00"
        DecimalFormat(pattern, DecimalFormatSymbols(Locale.US))
            .apply { roundingMode = java.math.RoundingMode.UNNECESSARY }
            .format(BigDecimal(value))
    } catch (_: Exception) {
        value
    }
}

internal fun transactionAmount(t: Transaction, fallbackCurrency: String, hidden: Boolean): String {
    val currency = t.currency ?: fallbackCurrency
    val amount = displayAmount(t.amount, currency, hidden)
    if (hidden || t.amount == null || t.direction !in listOf("debit", "credit")) return amount
    val prefix =
        when (t.direction) {
            "debit" -> "−"
            "credit" -> "+"
            else -> ""
        }
    val magnitude = amount.removePrefix("-").removePrefix("+")
    return "$prefix$magnitude"
}

internal fun shortBankName(bank: String): String =
    when {
        "招商" in bank -> "招行"
        "农业" in bank -> "农行"
        "工商" in bank -> "工行"
        "交通" in bank -> "交行"
        "建设" in bank -> "建行"
        "邮储" in bank || "邮政" in bank -> "邮储"
        "中国银行" in bank -> "中行"
        "兴业" in bank -> "兴业"
        "中信" in bank -> "中信"
        "广发" in bank -> "广发"
        "华夏" in bank -> "华夏"
        "浦发" in bank -> "浦发"
        "平安" in bank -> "平安"
        else -> bank
    }

internal fun accountTails(account: Account): String =
    when {
        account.tails.isEmpty() -> "无有效卡片"
        account.tails.size > 2 -> "${account.tails.size} 张卡"
        else -> account.tails.joinToString("、")
    }

internal fun monthTitle(date: String): Pair<String, String> =
    try {
        val parsed = LocalDate.parse(date)
        "${parsed.monthValue}月" to parsed.year.toString()
    } catch (_: Exception) {
        date.take(7) to ""
    }

internal fun shortDate(date: String?): String = date?.takeLast(5) ?: "未知"

internal fun canWrite(s: UiState): Boolean =
    s.statement?.let {
        !s.busy && !s.cached && s.pending == null && it.status == "confirmed" && it.account.active
    } ?: false

// A cached nested read must stay read-only when returning via Home to the retained history.
internal fun UiState.rememberHistoryRead(): UiState =
    when {
        screen == "details" -> copy(historyCached = cached, historyAt = cacheAt)
        screen == "detail" && history != null && historyAccountId == selectedAccount?.id ->
            copy(
                historyCached = historyCached || cached,
                historyAt = listOfNotNull(historyAt, cacheAt).minOrNull(),
            )
        else -> this
    }

// Every account entry starts from collapsed month summaries, never another account's detail.
internal fun UiState.openAccountHistory(account: Account): UiState =
    copy(
        screen = "details",
        selectedAccount = account,
        detailSection = "transactions",
        history = null,
        historyAccountId = null,
        statement = null,
        expandedStatementId = null,
        transactions = null,
        transactionsIncomplete = false,
        repayments = null,
        plans = null,
        cached = true,
        cacheAt = null,
        historyCached = true,
        historyAt = null,
        message = null,
    )

internal fun UiState.withHistoryPage(accountId: String, loaded: Loaded<Page<Statement>>): UiState =
    copy(
        history = loaded.data,
        historyAccountId = accountId,
        cached = loaded.cached,
        cacheAt = loaded.at,
        historyCached = loaded.cached,
        historyAt = loaded.at,
        statement = null,
        expandedStatementId = null,
        transactions = null,
        transactionsIncomplete = false,
        repayments = null,
        plans = null,
    )
