package dev.bills.app

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZonedDateTime
import org.json.JSONObject

private val Green = Color(0xFF245545)
private val Paper = Color(0xFFF8F5ED)

private fun shownTime(raw: String): String =
    try {
        OffsetDateTime.parse(raw).atZoneSameInstant(Rules.zone).format(Rules.dateTime)
    } catch (_: Exception) {
        raw
    }

private fun stamp(ms: Long?): String =
    ms?.let { Instant.ofEpochMilli(it).atZone(Rules.zone).format(Rules.dateTime) } ?: "未同步"

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BillsApp(model: BillsViewModel) {
    val s by model.state.collectAsStateWithLifecycle()
    MaterialTheme(
        colorScheme =
            lightColorScheme(
                primary = Green,
                background = Paper,
                surface = Paper,
                secondary = Color(0xFF786147),
            )
    ) {
        BackHandler(
            enabled = s.connection != null && s.screen !in listOf("home", "accounts", "settings")
        ) {
            model.back()
        }
        if (s.connection == null) PairScreen(s, model::pair)
        else {
            Scaffold(
                containerColor = Paper,
                bottomBar = {
                    NavigationBar(containerColor = Paper) {
                        listOf("home" to "账单", "accounts" to "账户", "settings" to "连接").forEach {
                            (route, title) ->
                            NavigationBarItem(
                                selected = s.screen == route,
                                onClick = {
                                    if (!s.busy) {
                                        if (route == "home") model.home() else model.tab(route)
                                    }
                                },
                                icon = {
                                    Text(
                                        if (route == "home") "账"
                                        else if (route == "accounts") "卡" else "连"
                                    )
                                },
                                label = { Text(title) },
                                enabled = !s.busy,
                            )
                        }
                    }
                },
            ) { padding ->
                PullToRefreshBox(
                    isRefreshing = s.busy,
                    onRefresh = model::refresh,
                    modifier = Modifier.fillMaxSize().padding(padding),
                ) {
                    LazyColumn(
                        Modifier.fillMaxSize().imePadding().padding(horizontal = 20.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        item {
                            Spacer(Modifier.height(12.dp))
                            Row(
                                Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                            ) {
                                Text(
                                    if (s.screen == "detail") "账单详情"
                                    else if (s.screen == "account") "账户档案" else "账序 / Bills",
                                    style = MaterialTheme.typography.headlineSmall,
                                    fontFamily = FontFamily.Serif,
                                )
                                TextButton(onClick = model::refresh, enabled = !s.busy) {
                                    Text("刷新")
                                }
                            }
                        }
                        if (s.screen in listOf("detail", "account"))
                            item {
                                TextButton(onClick = model::back, enabled = !s.busy) {
                                    Text("‹ 返回")
                                }
                            }
                        item {
                            Text(
                                "${if(s.cached) "缓存 / 非最新 · 离线只读" else "在线读取"} · 数据读取 ${stamp(s.cacheAt)}（北京时间）",
                                color = if (s.cached) MaterialTheme.colorScheme.error else Green,
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                        if (s.busy) item { LinearProgressIndicator(Modifier.fillMaxWidth()) }
                        s.message?.let { item { Notice(it) } }
                        s.pending?.let { p ->
                            item {
                                PendingCard(
                                    p,
                                    s.busy,
                                    { model.retry() },
                                    { model.retry(true) },
                                    model::cancelException,
                                )
                            }
                        }
                        when (s.screen) {
                            "home" -> homeContent(s, model)
                            "accounts" -> accountsContent(s, model)
                            "account" -> historyContent(s, model)
                            "detail" -> detailContent(s, model)
                            "settings" -> item { SettingsScreen(s, model::disconnect) }
                        }
                        item {
                            Text(
                                "仅为账单档案与还款标记；不执行支付，不确认银行到账。",
                                style = MaterialTheme.typography.bodySmall,
                            )
                            Spacer(Modifier.height(20.dp))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PairScreen(s: UiState, pair: (String, String, String) -> Unit) {
    var server by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var name by remember { mutableStateOf("我的手机") }
    LazyColumn(
        Modifier.fillMaxSize().background(Paper).systemBarsPadding().imePadding().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp),
    ) {
        item {
            Spacer(Modifier.height(40.dp))
            Text("账序", style = MaterialTheme.typography.displaySmall, fontFamily = FontFamily.Serif)
            Text("私人账单 · 清楚记录", color = Green)
        }
        item { Notice("在管理后台「安全设置」生成一次性配对码。手机只可看账单、记录还款和计划，不能管理银行、邮箱或模型。") }
        item {
            OutlinedTextField(
                server,
                { server = it },
                label = { Text("HTTPS 服务地址") },
                placeholder = { Text("https://bills.example.com") },
                singleLine = true,
                enabled = !s.busy,
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
        }
        item {
            OutlinedTextField(
                code,
                { code = it },
                label = { Text("一次性配对码") },
                singleLine = true,
                enabled = !s.busy,
                modifier = Modifier.fillMaxWidth(),
                visualTransformation = PasswordVisualTransformation(),
            )
        }
        item {
            OutlinedTextField(
                name,
                { name = it.take(80) },
                label = { Text("设备名称") },
                singleLine = true,
                enabled = !s.busy,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        s.message?.let { item { Notice(it) } }
        item {
            Button(
                onClick = {
                    pair(server, code, name)
                    code = ""
                },
                enabled = !s.busy && server.isNotBlank() && code.isNotBlank() && name.isNotBlank(),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(if (s.busy) "正在连接…" else "配对并连接")
            }
        }
        item {
            Text(
                "正式连接必须使用有效 HTTPS 证书。配对码仅显示本次，不会保存；凭证由手机安全存储保护。",
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun Notice(text: String) {
    Surface(color = Color(0xFFEDE6D6), shape = MaterialTheme.shapes.medium) {
        Text(text, Modifier.padding(14.dp), style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun PendingCard(
    p: Pending,
    busy: Boolean,
    retry: () -> Unit,
    confirm: () -> Unit,
    cancel: () -> Unit,
) {
    Card {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val body = remember(p.body) { JSONObject(p.body) }
            if (body.has("amount"))
                Text(
                    "${money(body.getString("amount"),body.getString("currency"))} · ${shownTime(body.getString("occurred_at"))}（北京时间）\n${body.optString("note")}"
                )
            if (body.has("reason")) Text("纠错原因：${body.getString("reason")}")
            Text(
                "${p.title} · ${if(p.needsConfirmation) "需要再次确认" else "尚未确认结果"}",
                fontWeight = FontWeight.Bold,
            )
            Text(
                if (p.needsConfirmation) "服务端要求核对超额金额或出账前时间。确认后仍不执行支付。"
                else "可能已写入，不能另建重复记录。重试沿用原内容和同一提交标识；关闭 App 后仍可手动核对重试。"
            )
            if (p.needsConfirmation) {
                Button(onClick = confirm, enabled = !busy) { Text("已核对，确认异常并提交") }
                TextButton(onClick = cancel, enabled = !busy) { Text("放弃本次未通过校验的提交") }
            } else Button(onClick = retry, enabled = !busy) { Text("核对重试（不新建）") }
        }
    }
}

private fun LazyListScope.homeContent(s: UiState, model: BillsViewModel) {
    val h = s.home
    if (h == null) {
        item { Notice("尚无可展示概览，请连接服务并刷新。") }
        return
    }
    item {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("本期记录待还", style = MaterialTheme.typography.titleMedium)
            if (h.totals.isEmpty())
                Text("金额未知 / 尚无已确认账单", style = MaterialTheme.typography.headlineSmall)
            h.totals.forEach {
                Text(
                    money(it.amount, it.currency),
                    style = MaterialTheme.typography.headlineMedium,
                    fontFamily = FontFamily.Serif,
                )
            }
            Text(h.disclaimer, style = MaterialTheme.typography.bodySmall)
            if (s.cached)
                Text("倒计时为服务端 ${h.asOf} 的快照，离线时不视为今日状态。", color = MaterialTheme.colorScheme.error)
        }
    }
    if (h.warnings.isNotEmpty())
        item { Notice(h.warnings.joinToString("\n") { "${it.accountName}：${it.message}（后台处理）" }) }
    item { HomeList(h, s.busy, model::detail) }
}

@Composable
private fun HomeList(h: Overview, busy: Boolean, open: (Statement) -> Unit) {
    var mode by remember { mutableStateOf("当前账单") }
    var bank by remember { mutableStateOf("全部银行") }
    var holder by remember { mutableStateOf("全部持卡人") }
    var banksOpen by remember { mutableStateOf(false) }
    var holdersOpen by remember { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        listOf("近期到期", "当前账单", "历史未结清").forEach { name ->
            FilterChip(
                selected = mode == name,
                onClick = { mode = name },
                label = { Text(name) },
                enabled = !busy,
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.weight(1f)) {
                OutlinedButton(
                    onClick = { banksOpen = true },
                    enabled = !busy,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(bank)
                }
                DropdownMenu(banksOpen, { banksOpen = false }) {
                    (listOf("全部银行") +
                            (h.latest + h.historical).map { it.account.bankName }.distinct())
                        .forEach { b ->
                            DropdownMenuItem(
                                text = { Text(b) },
                                onClick = {
                                    bank = b
                                    banksOpen = false
                                },
                            )
                        }
                }
            }
            Box(Modifier.weight(1f)) {
                OutlinedButton(
                    onClick = { holdersOpen = true },
                    enabled = !busy,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(holder)
                }
                DropdownMenu(holdersOpen, { holdersOpen = false }) {
                    (listOf("全部持卡人") +
                            (h.latest + h.historical).map { it.account.holderName }.distinct())
                        .forEach { n ->
                            DropdownMenuItem(
                                text = { Text(n) },
                                onClick = {
                                    holder = n
                                    holdersOpen = false
                                },
                            )
                        }
                }
            }
        }
        val rows =
            (if (mode == "历史未结清") h.historical else h.latest)
                .filter {
                    (bank == "全部银行" || it.account.bankName == bank) &&
                        (holder == "全部持卡人" || it.account.holderName == holder) &&
                        (mode != "近期到期" ||
                            (it.days != null &&
                                it.days <= 7 &&
                                it.paymentStatus !in listOf("settled", "overpaid")))
                }
                .sortedWith(compareBy<Statement> { it.due ?: "9999" }.thenByDescending { it.date })
        if (rows.isEmpty()) Notice("此筛选下暂无已确认账单。缺失金额请看上方异常提示；未知不等于零。")
        rows.forEach { StatementCard(it, busy, open) }
        if (mode == "历史未结清")
            Text(
                "历史未核对包括旧账期未标记结清；本期待确认 ${h.pending} 份请在后台核对。",
                style = MaterialTheme.typography.bodySmall,
            )
    }
}

@Composable
private fun StatementCard(row: Statement, busy: Boolean, open: (Statement) -> Unit) {
    OutlinedCard(onClick = { open(row) }, enabled = !busy, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                "${row.account.bankName} · ${row.account.holderName}",
                fontWeight = FontWeight.Bold,
            )
            Text(
                "${row.account.name} · ${row.date} · ${status(row.status)}",
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                "记录待还 ${money(row.remaining,row.currency)}",
                style = MaterialTheme.typography.titleLarge,
                fontFamily = FontFamily.Serif,
            )
            Text(
                "应还 ${money(row.amount,row.currency)} / 已标记 ${money(row.paid,row.currency)}",
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                "到期 ${row.due ?: "未知"} · ${row.days?.let { if(it<0) "已过期 ${-it} 天" else "剩余 $it 天" } ?: "日期待核对"}",
                color = if (row.overdue) MaterialTheme.colorScheme.error else Green,
            )
            Text(status(row.paymentStatus), style = MaterialTheme.typography.bodySmall)
        }
    }
}

private fun LazyListScope.accountsContent(s: UiState, model: BillsViewModel) {
    val page =
        s.accounts
            ?: run {
                item { Notice("账户尚未加载，请刷新。") }
                return
            }
    if (page.items.isEmpty()) item { Notice("暂无账户，请到管理后台添加。") }
    items(page.items, key = { it.id }) { a ->
        OutlinedCard(
            onClick = { model.account(a) },
            enabled = !s.busy,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(Modifier.padding(16.dp)) {
                Text("${a.bankName} · ${a.holderName}", fontWeight = FontWeight.Bold)
                Text(a.name)
                Text(accountHint(a))
                if (!a.active) Text("账户或上级已停用", color = MaterialTheme.colorScheme.error)
            }
        }
    }
    item { Pager(page, s.busy, model::accountsPage) }
}

private fun accountHint(a: Account): String =
    (if (a.mode == "merged") "多卡共用账单 / 合并还款" else "独立还款") +
        " · " +
        if (a.tails.isEmpty()) "无有效卡片" else a.tails.joinToString(" / ") { "尾号 $it" }

private fun LazyListScope.historyContent(s: UiState, model: BillsViewModel) {
    s.selectedAccount?.let { a ->
        item {
            Text("${a.bankName} · ${a.name}", style = MaterialTheme.typography.titleLarge)
            Text(accountHint(a))
        }
    }
    val page = s.history ?: return
    if (page.items.isEmpty()) item { Notice("暂无历史账单；不代表没有欠款。") }
    // Page-local month groups only. No all-history fetch.
    val groups = page.items.groupBy { it.date.take(7) }
    groups.forEach { (month, rows) ->
        item(key = month) { HistoryMonth(month, rows, s.busy, model::detail) }
    }
    item { Pager(page, s.busy, model::historyPage) }
}

@Composable
private fun HistoryMonth(
    month: String,
    rows: List<Statement>,
    busy: Boolean,
    open: (Statement) -> Unit,
) {
    var expanded by remember(month) { mutableStateOf(true) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        TextButton(onClick = { expanded = !expanded }) {
            Text("$month · ${rows.size} 份 ${if(expanded) "收起" else "展开"}")
        }
        if (expanded) rows.forEach { StatementCard(it, busy, open) }
    }
}

private fun LazyListScope.detailContent(s: UiState, model: BillsViewModel) {
    val row = s.statement ?: return
    item {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                "${row.account.bankName} · ${row.account.name}",
                style = MaterialTheme.typography.titleLarge,
            )
            Text(accountHint(row.account))
            Text("${row.account.holderName} · ${row.date} · 版本 ${row.version}")
            Text("${status(row.status)} / ${status(row.paymentStatus)}", color = Green)
            Text(
                "本期应还 ${money(row.amount,row.currency)}",
                style = MaterialTheme.typography.titleLarge,
            )
            Text(
                "已标记 ${money(row.paid,row.currency)}\n记录待还 ${money(row.remaining,row.currency)}\n最低还款 ${money(row.minimum,row.currency)}"
            )
            Text(
                "到期 ${row.due ?: "未知（不可作为确认日期）"}\n账期 ${row.periodStart ?: "未知"} 至 ${row.periodEnd ?: "未知"}"
            )
            Text(
                "来源：${if(row.source=="manual") "后台人工录入" else "导入账单 / 模型与人工核对"}。原邮件与证据仅后台可查看。",
                style = MaterialTheme.typography.bodySmall,
            )
            PaymentActions(row, s, model)
        }
    }
    item { DetailLists(s, model) }
}

@Composable
private fun PaymentActions(row: Statement, s: UiState, model: BillsViewModel) {
    var form by remember(row.id) { mutableStateOf<String?>(null) }
    val enabled =
        !s.busy && !s.cached && s.pending == null && row.status == "confirmed" && row.account.active
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { form = "payment" }, enabled = enabled, modifier = Modifier.weight(1f)) {
            Text("记录还款")
        }
        OutlinedButton(
            onClick = { form = "plan" },
            enabled = enabled,
            modifier = Modifier.weight(1f),
        ) {
            Text("计划还款")
        }
    }
    if (!enabled && !s.busy)
        Text("缓存只读、待核对提交或未确认/停用账户不能新建记录。", style = MaterialTheme.typography.bodySmall)
    form?.let { kind ->
        PaymentDialog(row.currency, kind == "plan", s.busy, { form = null }) { amount, time, note ->
            model.payment(kind == "plan", amount, time, note)
            form = null
        }
    }
}

@Composable
private fun PaymentDialog(
    currency: String,
    plan: Boolean,
    busy: Boolean,
    dismiss: () -> Unit,
    submit: (String, String, String) -> Unit,
) {
    var amount by remember { mutableStateOf("") }
    var time by remember {
        mutableStateOf(
            ZonedDateTime.now(Rules.zone).plusDays(if (plan) 1 else 0).format(Rules.dateTime)
        )
    }
    var note by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    AlertDialog(
        onDismissRequest = { if (!busy) dismiss() },
        title = { Text(if (plan) "保存计划（不是已还款）" else "记录还款（不执行支付）") },
        text = {
            Column(
                Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(if (plan) "计划不减少待还金额。通知功能尚未实现，保存后不会安排提醒。" else "请填写实际还款金额与时间，仅作个人标记，不证明银行到账。")
                OutlinedTextField(
                    amount,
                    { amount = it },
                    label = { Text("金额 / $currency") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                )
                OutlinedTextField(
                    time,
                    { time = it },
                    label = { Text("北京时间 yyyy-MM-dd HH:mm") },
                    singleLine = true,
                )
                OutlinedTextField(
                    note,
                    { note = it.take(500) },
                    label = { Text("备注（可选）") },
                    maxLines = 3,
                )
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    try {
                        Rules.amount(amount, currency)
                        Rules.instant(time)
                        submit(amount, time, note)
                    } catch (e: IllegalArgumentException) {
                        error = e.message
                    }
                },
                enabled = !busy,
            ) {
                Text("在线提交")
            }
        },
        dismissButton = { TextButton(onClick = dismiss, enabled = !busy) { Text("取消") } },
    )
}

@Composable
private fun DetailLists(s: UiState, model: BillsViewModel) {
    var tab by remember(s.statement?.id) { mutableStateOf("详单") }
    var action by remember { mutableStateOf<Pair<Boolean, PaymentRecord>?>(null) }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf("详单", "还款", "计划").forEach { name ->
                FilterChip(selected = tab == name, onClick = { tab = name }, label = { Text(name) })
            }
        }
        if (tab == "详单") {
            Text(detailStatus(s.statement?.transactionStatus ?: "not_provided"))
            val page = s.transactions
            if (page == null) Text("此页尚未读取成功，请刷新或联网后重试。")
            else {
                if (page.items.isEmpty()) Text("本页无已提取交易；请结合完整性状态判断。")
                page.items.forEach { t ->
                    HorizontalDivider()
                    Text(t.description.ifBlank { "交易摘要未知" })
                    Text(
                        "${t.date ?: "交易日期未知"} / 入账 ${t.posted ?: "未知"} · ${t.tail?.let { "尾号 $it" } ?: "卡片未知"}",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Text(
                        "${money(t.amount,t.currency ?: "币种未知")} · ${when(t.direction){"debit"->"支出"
"credit"->"贷方 / 入账"
else->"方向未知"}}"
                    )
                }
                Pager(page, s.busy, model::transactionsPage)
            }
        } else {
            val plan = tab == "计划"
            val page = if (plan) s.plans else s.repayments
            if (plan) Notice("计划不会减少记录待还；未实施通知投递。")
            if (page == null) Text("记录尚未读取成功，请刷新。")
            else {
                if (page.items.isEmpty()) Text("暂无${if(plan) "计划" else "还款标记"}记录。")
                page.items.forEach { record ->
                    HorizontalDivider()
                    Text(money(record.amount, record.currency), fontWeight = FontWeight.Bold)
                    Text(shownTime(record.time) + "（北京）")
                    if (record.note.isNotEmpty()) Text(record.note)
                    if (record.endedAt != null)
                        Text(
                            "${if(plan) "已取消" else "已撤销"} · ${record.reason ?: ""}",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    else
                        TextButton(
                            onClick = { action = plan to record },
                            enabled = !s.busy && !s.cached && s.pending == null,
                        ) {
                            Text(if (plan) "取消计划" else "纠错 / 撤销标记")
                        }
                }
                Pager(page, s.busy) { model.recordsPage(plan, it) }
            }
        }
    }
    action?.let { (plan, record) ->
        ReasonDialog(if (plan) "取消计划" else "撤销还款标记", { action = null }) { reason ->
            model.revoke(plan, record.id, reason)
            action = null
        }
    }
}

@Composable
private fun ReasonDialog(title: String, dismiss: () -> Unit, submit: (String) -> Unit) {
    var reason by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = dismiss,
        title = { Text(title) },
        text = {
            Column {
                Text("不会撤销真实银行转账。原记录将保留以供核对。")
                OutlinedTextField(reason, { reason = it.take(500) }, label = { Text("请填写原因") })
            }
        },
        confirmButton = {
            TextButton(onClick = { submit(reason) }, enabled = reason.isNotBlank()) {
                Text("确认并在线提交")
            }
        },
        dismissButton = { TextButton(onClick = dismiss) { Text("返回") } },
    )
}

@Composable
private fun <T> Pager(page: Page<T>, busy: Boolean, go: (Int) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        TextButton(onClick = { go(page.page - 1) }, enabled = !busy && page.page > 1) {
            Text("上一页")
        }
        Text(
            "第 ${page.page} 页 / 共 ${page.total} 条",
            Modifier.padding(top = 12.dp),
            style = MaterialTheme.typography.bodySmall,
        )
        TextButton(onClick = { go(page.page + 1) }, enabled = !busy && page.hasNext) { Text("下一页") }
    }
}

@Composable
private fun SettingsScreen(s: UiState, disconnect: () -> Unit) {
    var ask by remember { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("连接与隐私", style = MaterialTheme.typography.titleLarge)
        Text(
            "服务 ${s.connection?.server}\n设备 ${s.connection?.name}\n凭证到期 ${s.connection?.expiresAt?.let(::shownTime)}（北京）\n版本 ${BuildConfig.VERSION_NAME}"
        )
        Notice("通知：尚未实现（P5）。没有后台自动同步或离线写队列；打开 App 或点击刷新只读取 VPS，不触发收信或模型识别。")
        Text("缓存最多保留200个页面，超过30天不再作为离线数据使用。已查看历史可离线读取，未查看页面须联网。")
        Text(
            "配对凭证和缓存已加密，禁止系统备份与截屏。退出会清理本机数据，并尝试撤销远端设备授权。",
            style = MaterialTheme.typography.bodySmall,
        )
        OutlinedButton(onClick = { ask = true }, enabled = !s.busy) { Text("断开连接 / 更换服务") }
    }
    if (ask)
        AlertDialog(
            onDismissRequest = { ask = false },
            title = { Text("清理本机并断开？") },
            text = {
                Text(
                    "${if(s.pending!=null) "有未确认提交，可能已经写入，请先在后台核对。" else "账单仍保存在 VPS。"}离线时无法确认远端撤销，需在后台撤销此设备。"
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        ask = false
                        disconnect()
                    }
                ) {
                    Text("确认清理并断开")
                }
            },
            dismissButton = { TextButton(onClick = { ask = false }) { Text("返回") } },
        )
}
