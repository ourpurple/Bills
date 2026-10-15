package dev.bills.app

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

internal fun LazyListScope.homeContent(
    s: UiState,
    actions: UiActions,
    bank: String,
    holder: String,
    mode: String,
    recordPayment: (Statement) -> Unit,
) {
    val h = s.home
    if (h == null) {
        item { EmptyState("账单尚未读取", "请连接服务并刷新；没有数据不代表没有欠款。", actions.refresh, s.busy) }
        return
    }
    item(key = "overview") {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(
                Modifier.fillMaxWidth().testTag("overview-summary"),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    "全部待还",
                    color = Muted,
                    fontSize = 15.sp,
                    modifier = Modifier.weight(1f).testTag("overview-label"),
                )
                Column(
                    Modifier.weight(2f).testTag("overview-totals"),
                    horizontalAlignment = Alignment.End,
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    if (h.totals.isEmpty())
                        Text("金额未知 / 尚无已确认账单", fontSize = 20.sp, textAlign = TextAlign.End)
                    h.totals.forEach { total ->
                        Text(
                            buildAnnotatedString {
                                withStyle(SpanStyle(color = Muted, fontSize = 13.sp)) {
                                    append("${total.currency}  ")
                                }
                                append(displayAmount(total.amount, total.currency, s.amountsHidden))
                            },
                            fontSize = 26.sp,
                            textAlign = TextAlign.End,
                            modifier =
                                Modifier.fillMaxWidth().testTag("overview-total-${total.currency}"),
                        )
                    }
                }
            }
            if (bank.isNotEmpty() || holder.isNotEmpty())
                Text(
                    "列表筛选：${bank.ifEmpty { "全部银行" }} / ${holder.ifEmpty { "全部持卡人" }}（上方仍为全部账户汇总）",
                    color = Blue,
                    fontSize = 11.sp,
                )
            if (s.cached) Text("到期倒计时为服务器 ${h.asOf} 的快照，不代表今日状态。", color = Coral, fontSize = 11.sp)
        }
        HorizontalDivider(color = Line)
    }
    val rows =
        (if (mode == "history") h.historical else h.latest)
            .filter {
                (bank.isEmpty() || it.account.bankName == bank) &&
                    (holder.isEmpty() || it.account.holderName == holder) &&
                    (mode != "upcoming" ||
                        (it.days != null &&
                            it.days <= 7 &&
                            it.paymentStatus !in listOf("settled", "overpaid")))
            }
            .sortedWith(
                compareBy<Statement> { it.paymentStatus in listOf("settled", "overpaid") }
                    .thenBy { it.due ?: "9999" }
                    .thenByDescending { it.date }
            )
    items(rows, key = { "bill-${it.id}" }) { row -> StatementRow(row, s, actions, recordPayment) }
    val missing =
        if (mode == "current")
            s.accounts?.items.orEmpty().filter { a ->
                a.active &&
                    h.latest.none { it.account.id == a.id } &&
                    (bank.isEmpty() || a.bankName == bank) &&
                    (holder.isEmpty() || a.holderName == holder)
            }
        else emptyList()
    items(missing, key = { "missing-${it.id}" }) { account ->
        MissingStatementRow(account, s.busy, actions)
    }
    if (rows.isEmpty() && missing.isEmpty())
        item { EmptyState("暂无匹配账单", "未知金额不等于零，缺失账单请在现有 Web 管理端核对。") }
    item(key = "home-disclaimer") {
        Column(
            Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            if (mode == "history")
                Text("旧账期未结清单独展示，不直接累加到本期汇总，避免结转重复计算。", color = Muted, fontSize = 12.sp)
            if (h.pending > 0)
                Text("${h.pending} 份待确认账单，请在现有 Web 管理端核对。", color = Coral, fontSize = 12.sp)
            Text(h.disclaimer, color = Muted, fontSize = 11.sp)
            Text("刷新仅读取服务器已有数据，不触发收信或模型识别。", color = Muted, fontSize = 11.sp)
            if (s.accounts?.hasNext == true)
                TextButton(onClick = { actions.tab("details") }, enabled = !s.busy) {
                    Text("在详单中选择更多账户")
                }
        }
    }
}

@Composable
internal fun AccountIdentity(account: Account, modifier: Modifier = Modifier) {
    Row(
        modifier,
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        BankBadge(account.bankName)
        Text(
            buildAnnotatedString {
                withStyle(SpanStyle(color = Ink)) { append(shortBankName(account.bankName)) }
                withStyle(SpanStyle(color = Muted)) {
                    append("  ${account.holderName} ${accountTails(account)}")
                }
            },
            fontSize = 16.sp,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun StatementRow(
    row: Statement,
    s: UiState,
    actions: UiActions,
    recordPayment: (Statement) -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxWidth()
            .clickable(enabled = !s.busy) { actions.account(row.account) }
            .testTag("bill-${row.id}")
            .padding(horizontal = 16.dp, vertical = 8.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            AccountIdentity(row.account, Modifier.weight(1f))
            Box {
                IconButton(onClick = { menu = true }, enabled = !s.busy) {
                    AppIcon("more", Color(0xFFCDCDD2), Modifier.size(20.dp))
                }
                DropdownMenu(menu, { menu = false }) {
                    DropdownMenuItem(
                        text = { Text("查看账单详情") },
                        onClick = {
                            menu = false
                            actions.detail(row)
                        },
                    )
                    DropdownMenuItem(
                        text = { Text("查看账户历史") },
                        onClick = {
                            menu = false
                            actions.account(row.account)
                        },
                    )
                }
            }
        }
        Spacer(Modifier.height(6.dp))
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val largeFont = LocalDensity.current.fontScale > 1.2f
            val compactDue = maxWidth < 340.dp
            // Keep the caption beside the number even when accessibility text needs more room.
            if (maxWidth < 280.dp || largeFont) {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    StatementBalance(row, s.amountsHidden)
                    Row(
                        Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        DueStatus(row, Modifier.weight(1f), compactDue)
                        if (hasRepayableBalance(row))
                            HomeRepaymentButton(row, canRecordHomePayment(s, row)) {
                                recordPayment(row)
                            }
                    }
                }
            } else {
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    StatementBalance(row, s.amountsHidden, Modifier.weight(1.05f))
                    DueStatus(row, Modifier.weight(1.2f), compactDue)
                    if (hasRepayableBalance(row))
                        HomeRepaymentButton(row, canRecordHomePayment(s, row)) {
                            recordPayment(row)
                        }
                }
            }
        }
        if (!row.account.active)
            Text(
                "账户或上级已停用 · 旧账单仍保留",
                color = Muted,
                fontSize = 11.sp,
                modifier = Modifier.padding(top = 10.dp),
            )
    }
    HorizontalDivider(color = Line)
}

@Composable
private fun StatementBalance(
    row: Statement,
    amountsHidden: Boolean,
    modifier: Modifier = Modifier,
) {
    Column(modifier) {
        Text(
            displayAmount(row.remaining, row.currency, amountsHidden),
            fontSize = 19.sp,
            color = if (row.remaining == null) Muted else Ink,
        )
        Text(
            "${if(row.remaining==null) "待更新 · " else ""}本期待还 · ${row.currency}",
            color = Muted,
            fontSize = 11.sp,
        )
        if (row.paymentStatus == "partial") Text("已标记部分还款", fontSize = 10.sp, color = Blue)
    }
}

@Composable
private fun HomeRepaymentButton(row: Statement, enabled: Boolean, onClick: () -> Unit) {
    // A flatter face without shrinking the explicit 48dp touch target.
    Box(
        Modifier.widthIn(min = 60.dp)
            .heightIn(min = 48.dp)
            .clickable(
                enabled = enabled,
                role = Role.Button,
                onClickLabel = "记录还款",
                onClick = onClick,
            )
            .testTag("home-pay-${row.id}"),
        contentAlignment = Alignment.Center,
    ) {
        Surface(
            modifier = Modifier.heightIn(min = 36.dp).testTag("home-pay-face-${row.id}"),
            shape = RoundedCornerShape(12.dp),
            color =
                if (enabled) Yellow else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f),
            contentColor =
                if (enabled) Ink else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
        ) {
            Box(
                Modifier.padding(horizontal = 14.dp, vertical = 6.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text("还款", fontSize = 13.sp)
            }
        }
    }
}

@Composable
private fun DueStatus(row: Statement, modifier: Modifier, compact: Boolean = false) {
    val due = dueLabel(row)
    val color = if (due.urgent) Coral else Ink
    val valueSize = if (compact) 26.sp else 30.sp
    val captionSize = if (compact) 11.sp else 12.sp
    val captionLineHeight = if (compact) 12.sp else 13.sp
    // Do not inherit bodyLarge's 24sp line height for these small labels.
    val tightTextStyle =
        TextStyle(
            platformStyle = PlatformTextStyle(includeFontPadding = false),
            lineHeightStyle =
                LineHeightStyle(
                    alignment = LineHeightStyle.Alignment.Center,
                    trim = LineHeightStyle.Trim.None,
                ),
        )
    Row(
        modifier.height(IntrinsicSize.Min).testTag("due-status-${row.id}"),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        Text(
            due.value,
            modifier = Modifier.testTag("due-value-${row.id}"),
            style = tightTextStyle,
            fontSize = valueSize,
            lineHeight = valueSize,
            color = color,
            maxLines = 1,
        )
        Column(
            Modifier.weight(1f).fillMaxHeight(),
            verticalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                due.caption,
                modifier = Modifier.testTag("due-caption-${row.id}"),
                style = tightTextStyle,
                color = color,
                fontSize = captionSize,
                lineHeight = captionLineHeight,
            )
            Text(
                shortDate(row.due),
                modifier = Modifier.testTag("due-date-${row.id}"),
                style = tightTextStyle,
                color = Muted,
                fontSize = 11.sp,
                lineHeight = captionLineHeight,
            )
        }
    }
}

@Composable
private fun MissingStatementRow(account: Account, busy: Boolean, actions: UiActions) {
    Column(
        Modifier.fillMaxWidth()
            .clickable(enabled = !busy) { actions.account(account) }
            .padding(horizontal = 16.dp, vertical = 12.dp)
    ) {
        AccountIdentity(account)
        Spacer(Modifier.height(10.dp))
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("待更新", color = Muted, fontSize = 18.sp)
                Text("尚无本期已确认账单 · 到期日未知", color = Muted, fontSize = 11.sp)
            }
        }
    }
    HorizontalDivider(color = Line)
}

@Composable
internal fun HomeFilterDialog(
    s: UiState,
    bank: String,
    holder: String,
    mode: String,
    setBank: (String) -> Unit,
    setHolder: (String) -> Unit,
    setMode: (String) -> Unit,
    dismiss: () -> Unit,
) {
    val accounts =
        (s.home?.latest.orEmpty() + s.home?.historical.orEmpty()).map { it.account } +
            s.accounts?.items.orEmpty()
    AlertDialog(
        onDismissRequest = dismiss,
        title = { Text("筛选账单") },
        text = {
            Column(
                Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                val modes =
                    listOf("current" to "本期账单", "upcoming" to "近期到期（7天内）", "history" to "历史未结清")
                ChoiceMenu(
                    "账单范围",
                    modes.firstOrNull { it.first == mode }?.second ?: "本期账单",
                    modes,
                    setMode,
                )
                ChoiceMenu(
                    "银行",
                    bank.ifEmpty { "全部银行" },
                    listOf("" to "全部银行") + accounts.map { it.bankName }.distinct().map { it to it },
                    setBank,
                )
                ChoiceMenu(
                    "持卡人",
                    holder.ifEmpty { "全部持卡人" },
                    listOf("" to "全部持卡人") +
                        accounts.map { it.holderName }.distinct().map { it to it },
                    setHolder,
                )
                Text("仅筛选列表；汇总仍显示全部账户，外币不会折算相加。", color = Muted, fontSize = 12.sp)
            }
        },
        confirmButton = { TextButton(onClick = dismiss) { Text("完成") } },
        dismissButton = {
            TextButton(
                onClick = {
                    setBank("")
                    setHolder("")
                    setMode("current")
                }
            ) {
                Text("重置")
            }
        },
    )
}

@Composable
internal fun ChoiceMenu(
    title: String,
    value: String,
    options: List<Pair<String, String>>,
    choose: (String) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    Column {
        Text(title, color = Muted, fontSize = 12.sp)
        Box {
            OutlinedButton(onClick = { open = true }, modifier = Modifier.fillMaxWidth()) {
                Text(value, modifier = Modifier.weight(1f))
                AppIcon("down", Muted, Modifier.size(18.dp))
            }
            DropdownMenu(open, { open = false }) {
                options.forEach { (id, name) ->
                    DropdownMenuItem(
                        text = { Text(name) },
                        onClick = {
                            choose(id)
                            open = false
                        },
                    )
                }
            }
        }
    }
}
