package dev.bills.app

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

internal fun LazyListScope.historyContent(
    s: UiState,
    actions: UiActions,
    selectAccount: () -> Unit,
) {
    item(key = "detail-tabs") { DetailTabs(s.detailSection, !s.busy, actions.section) }
    val account = s.selectedAccount
    if (account == null) {
        item { EmptyState("选择还款账户", "按银行、持卡人和卡片尾号查看月份详单。", selectAccount, s.busy) }
        return
    }
    val page = s.history
    if (page == null) {
        item {
            EmptyState(if (s.busy) "正在读取账期" else "账期读取失败", "未查看的历史需要联网读取。", actions.refresh, s.busy)
        }
        return
    }
    if (page.items.isEmpty()) item { EmptyState("暂无历史账单", "没有账单不代表没有欠款，请在现有 Web 管理端核对。") }
    page.items.forEach { row ->
        val expanded = s.expandedStatementId == row.id
        item(key = "month-${row.id}") {
            MonthHeader(row, s.amountsHidden, expanded, !s.busy) { actions.expand(row) }
        }
        if (expanded && s.statement?.id == row.id) {
            detailItems(s, actions, prefix = row.id)
        }
    }
    if (!s.busy && page.items.size < page.total) {
        item(key = "history-incomplete") {
            Text(
                "月份账单尚未完整读取，请下拉重试",
                color = Coral,
                fontSize = 11.sp,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            )
        }
    }
}

internal fun LazyListScope.statementContent(s: UiState, actions: UiActions) {
    val row = s.statement
    if (row == null) {
        item { EmptyState("账单无法读取", "请返回并刷新后重试。", actions.refresh, s.busy) }
        return
    }
    item(key = "detail-tabs") { DetailTabs(s.detailSection, !s.busy, actions.section) }
    val expanded = s.expandedStatementId == row.id
    item(key = "month") {
        MonthHeader(row, s.amountsHidden, expanded, !s.busy) { actions.expand(row) }
    }
    if (s.cached || s.pending != null || !row.account.active || row.status != "confirmed")
        item(key = "read-only") {
            Text(
                "当前为只读：缓存、待核对提交或未确认 / 停用账户不能新建记录。",
                fontSize = 11.sp,
                color = Coral,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            )
        }
    if (expanded) {
        detailItems(s, actions, prefix = "detail")
    }
}

@Composable
internal fun DetailTabs(section: String, enabled: Boolean, select: (String) -> Unit) {
    Row(Modifier.fillMaxWidth().background(Color.White).padding(horizontal = 8.dp)) {
        listOf("transactions" to "账单明细", "repayments" to "还款记录").forEach { (id, title) ->
            val selected = if (id == "transactions") section == id else section != "transactions"
            Column(
                Modifier.clickable(enabled = enabled) { select(id) }
                    .testTag("section-$id")
                    .heightIn(min = 48.dp)
                    .padding(horizontal = 8.dp, vertical = 8.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(title, color = if (selected) Ink else Muted, fontSize = 18.sp)
                Spacer(Modifier.height(6.dp))
                Box(
                    Modifier.width(25.dp)
                        .height(3.dp)
                        .background(
                            if (selected) Yellow else Color.Transparent,
                            RoundedCornerShape(2.dp),
                        )
                )
            }
        }
    }
}

@Composable
internal fun MonthHeader(
    row: Statement,
    hidden: Boolean,
    expanded: Boolean,
    enabled: Boolean,
    toggle: () -> Unit,
) {
    val (month, year) = monthTitle(row.date)
    Column {
        Row(
            Modifier.fillMaxWidth()
                .clickable(enabled = enabled) { toggle() }
                .testTag("month-${row.id}")
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            AppIcon(if (expanded) "up" else "down", Color(0xFFCECED2), Modifier.size(16.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Row(
                    verticalAlignment = Alignment.Bottom,
                    horizontalArrangement = Arrangement.spacedBy(5.dp),
                ) {
                    Text(month, fontSize = 20.sp)
                    Text(year, color = Muted, fontSize = 12.sp)
                }
                Text(
                    "${shortDate(row.periodStart)} 至 ${shortDate(row.periodEnd)}",
                    color = Muted,
                    fontSize = 13.sp,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    if (row.amount == null && !hidden) "待更新"
                    else displayAmount(row.amount, row.currency, hidden),
                    fontSize = 21.sp,
                    color = if (row.amount == null) Muted else Ink,
                )
                Text(row.currency, color = Muted, fontSize = 10.sp)
            }
        }
        HorizontalDivider(color = Line, modifier = Modifier.padding(horizontal = 16.dp))
    }
}

internal fun LazyListScope.detailItems(s: UiState, actions: UiActions, prefix: String) {
    val row = s.statement ?: return
    if (s.detailSection == "transactions") {
        val page = s.transactions
        val noDetails =
            page != null && page.items.isEmpty() && page.total == 0 && !s.transactionsIncomplete
        val extractionStatus =
            listOfNotNull(row.transactionStatus, s.transactions?.detailStatus).firstOrNull {
                it != "complete"
            }
        if (extractionStatus != null && !noDetails)
            item(key = "$prefix-status") {
                Text(
                    detailStatus(extractionStatus),
                    color = Muted,
                    fontSize = 11.sp,
                    modifier =
                        Modifier.fillMaxWidth()
                            .background(Soft)
                            .padding(horizontal = 16.dp, vertical = 10.dp),
                )
            }
        if (page == null)
            item(key = "$prefix-read-failed") {
                EmptyState(
                    if (s.busy) "正在读取详单" else "详单尚未读取成功",
                    "请联网刷新后重试，不虚构交易明细。",
                    actions.refresh,
                    s.busy,
                )
            }
        else {
            if (page.items.isEmpty())
                item(key = "$prefix-empty") {
                    EmptyState(
                        if (page.detailStatus == "complete") "暂无交易" else "尚无可展示详单",
                        if (noDetails) ""
                        else if (page.detailStatus == "complete") "该账单没有已提取交易；仍以银行账单为准。"
                        else "未提供或未完整提取详单，不等于没有消费。",
                    )
                }
            // Index keys preserve separate identical transactions and the server's original order.
            itemsIndexed(page.items, key = { index, _ -> "$prefix-tx-${page.page}-$index" }) {
                index,
                t ->
                Box(Modifier.testTag("$prefix-tx-${page.page}-$index")) {
                    TransactionRow(t, row.currency, s.amountsHidden)
                }
            }
            if (!noDetails) {
                item(key = "$prefix-transaction-count") {
                    Column(
                        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Text(
                            "共 ${page.total} 条",
                            color = Muted,
                            fontSize = 12.sp,
                            modifier = Modifier.testTag("transaction-count"),
                        )
                        if (s.transactionsIncomplete || page.items.size != page.total) {
                            Text(
                                if (s.busy) "正在读取，已显示 ${page.items.size} 条…"
                                else "仅显示 ${page.items.size} 条，未完整读取，请下拉重试",
                                color = if (s.busy) Muted else Coral,
                                fontSize = 11.sp,
                            )
                        }
                    }
                }
            }
        }
    } else {
        val plan = s.detailSection == "plans"
        item(key = "$prefix-record-switch") {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                FilterChip(
                    selected = !plan,
                    onClick = { actions.section("repayments") },
                    enabled = !s.busy,
                    label = { Text("已还款标记") },
                )
                FilterChip(
                    selected = plan,
                    onClick = { actions.section("plans") },
                    enabled = !s.busy,
                    label = { Text("还款计划") },
                )
            }
        }
        if (plan)
            item(key = "$prefix-plan-notice") {
                Notice(
                    "计划不会减少待还金额。通知尚未实现，保存计划不会安排提醒。",
                    Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                )
            }
        val page = if (plan) s.plans else s.repayments
        if (page == null) item { EmptyState("记录尚未读取成功", "请联网刷新。", actions.refresh, s.busy) }
        else {
            if (page.items.isEmpty())
                item { EmptyState(if (plan) "暂无还款计划" else "暂无还款标记", "这些记录仅供个人核对，不执行支付、不确认银行到账。") }
            items(page.items, key = { "$prefix-record-${it.id}" }) { record ->
                RecordRow(record, plan, s, actions)
            }
            item(key = "$prefix-record-pager") {
                Box(Modifier.padding(horizontal = 16.dp, vertical = 10.dp)) {
                    Pager(page, s.busy) { actions.recordsPage(plan, it) }
                }
            }
        }
    }
}

@Composable
internal fun TransactionRow(t: Transaction, currency: String, hidden: Boolean) {
    Row(
        Modifier.fillMaxWidth().background(Soft).padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(
            Modifier.size(24.dp)
                .background(
                    if (t.direction == "credit") Color(0xFFE1F3ED) else Color(0xFFFFF0CB),
                    CircleShape,
                ),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                if (t.direction == "credit") "+" else "¥",
                color = if (t.direction == "credit") Color(0xFF39A487) else Color(0xFFEEAF1B),
                fontSize = 16.sp,
            )
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(t.description.ifBlank { "交易摘要未知" }, fontSize = 14.sp)
            Text(
                "${shortDate(t.date)} · ${when(t.direction) {"debit"->"支出"
"credit"->"贷方 / 入账"
else->"方向未知"}} ${t.tail?.let {"[$it]"} ?: "· 尾号未知"}",
                color = Muted,
                fontSize = 11.sp,
            )
            if (t.posted != null && t.posted != t.date)
                Text("入账 ${shortDate(t.posted)}", color = Muted, fontSize = 11.sp)
        }
        Column(horizontalAlignment = Alignment.End, modifier = Modifier.widthIn(max = 145.dp)) {
            Text(
                transactionAmount(t, currency, hidden),
                fontSize = 18.sp,
                color = if (t.direction == "credit") Color(0xFF39A487) else Ink,
            )
            Text(t.currency ?: currency, color = Muted, fontSize = 10.sp)
        }
    }
}

@Composable
private fun RecordRow(record: PaymentRecord, plan: Boolean, s: UiState, actions: UiActions) {
    var reason by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "${record.currency} ${displayAmount(record.amount,record.currency,s.amountsHidden)}",
                fontSize = 20.sp,
                modifier = Modifier.weight(1f),
            )
            Text(
                if (record.endedAt != null) (if (plan) "已取消" else "已撤销")
                else (if (plan) "计划中" else "已标记"),
                color = Muted,
                fontSize = 12.sp,
            )
        }
        Text("${shownTime(record.time)}（北京时间）", color = Muted, fontSize = 12.sp)
        if (record.note.isNotBlank()) Text(record.note, fontSize = 13.sp)
        if (record.endedAt != null)
            Text(
                "${shownTime(record.endedAt)} · ${record.reason ?: "原因未提供"}",
                color = Muted,
                fontSize = 12.sp,
            )
        else
            TextButton(onClick = { reason = true }, enabled = canWrite(s)) {
                Text(if (plan) "取消计划" else "撤销还款标记")
            }
    }
    HorizontalDivider(color = Line, modifier = Modifier.padding(horizontal = 16.dp))
    if (reason)
        ReasonDialog(if (plan) "取消计划" else "撤销还款标记", { reason = false }) {
            actions.revoke(plan, record.id, it)
            reason = false
        }
}

@Composable
internal fun PaymentFooter(s: UiState, plan: () -> Unit, payment: () -> Unit) {
    Column(Modifier.fillMaxWidth().background(Color.White).navigationBarsPadding()) {
        HorizontalDivider(color = Line)
        Text(
            "仅作个人标记 · 不执行支付 · 不确认银行到账",
            color = Muted,
            fontSize = 10.sp,
            modifier = Modifier.padding(start = 16.dp, top = 6.dp),
        )
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            TextButton(
                onClick = plan,
                enabled = canWrite(s),
                modifier = Modifier.weight(.8f).heightIn(min = 48.dp),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    AppIcon("clock", if (canWrite(s)) Blue else Muted, Modifier.size(20.dp))
                    Text("计划还款", fontSize = 12.sp)
                }
            }
            Button(
                onClick = payment,
                enabled = canWrite(s),
                modifier = Modifier.weight(1.8f).heightIn(min = 48.dp).testTag("mark-payment"),
                shape = RoundedCornerShape(50),
                colors = ButtonDefaults.buttonColors(containerColor = Yellow, contentColor = Ink),
            ) {
                Text("标记已还款", fontSize = 17.sp, fontWeight = FontWeight.Medium)
            }
        }
    }
}

@Composable
internal fun StatementInfo(row: Statement, hidden: Boolean, dismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = dismiss,
        title = { Text("账单信息") },
        text = {
            Column(
                Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    "${row.account.bankName} · ${row.account.holderName}\n${row.account.name}\n${accountTails(row.account)}"
                )
                Text(if (row.account.mode == "merged") "多卡共用账单 / 合并还款，只计算一次" else "独立还款账户")
                Text(
                    "出账 ${row.date} · 版本 ${row.version}\n到期 ${row.due ?: "未知"}\n账期 ${row.periodStart ?: "未知"} 至 ${row.periodEnd ?: "未知"}"
                )
                Text(
                    "本期应还 ${row.currency} ${displayAmount(row.amount,row.currency,hidden)}\n已标记 ${displayAmount(row.paid,row.currency,hidden)}\n记录待还 ${displayAmount(row.remaining,row.currency,hidden)}\n最低还款 ${displayAmount(row.minimum,row.currency,hidden)}"
                )
                Text("${status(row.status)} / ${status(row.paymentStatus)}")
                Text(
                    "来源：${if(row.source=="manual") "后台人工录入" else "导入账单 / 模型与人工核对"}。原邮件与证据仅后台可查看。",
                    color = Muted,
                    fontSize = 12.sp,
                )
            }
        },
        confirmButton = { TextButton(onClick = dismiss) { Text("关闭") } },
    )
}
