package dev.bills.app

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.time.Instant
import java.time.OffsetDateTime

internal fun shownTime(raw: String): String =
    try {
        OffsetDateTime.parse(raw).atZoneSameInstant(Rules.zone).format(Rules.dateTime)
    } catch (_: Exception) {
        raw
    }

internal fun stamp(ms: Long?): String =
    ms?.let { Instant.ofEpochMilli(it).atZone(Rules.zone).format(Rules.dateTime) } ?: "未同步"

internal class UiActions(
    val tab: (String) -> Unit = {},
    val refresh: () -> Unit = {},
    val back: () -> Unit = {},
    val pair: (String, String, String) -> Unit = { _, _, _ -> },
    val detail: (Statement) -> Unit = {},
    val account: (Account) -> Unit = {},
    val expand: (Statement) -> Unit = {},
    val accountsPage: (Int) -> Unit = {},
    val recordsPage: (Boolean, Int) -> Unit = { _, _ -> },
    val section: (String) -> Unit = {},
    val payment: (Boolean, String, String, String) -> Unit = { _, _, _, _ -> },
    val revoke: (Boolean, String, String) -> Unit = { _, _, _ -> },
    val retry: (Boolean) -> Unit = {},
    val cancelException: () -> Unit = {},
    val hideAmounts: (Boolean) -> Unit = {},
    val defaultAccount: (String?) -> Unit = {},
    val disconnect: () -> Unit = {},
)

@Composable
fun BillsApp(model: BillsViewModel) {
    val s by model.state.collectAsStateWithLifecycle()
    val actions =
        remember(model) {
            UiActions(
                tab = model::tab,
                refresh = model::refresh,
                back = model::back,
                pair = model::pair,
                detail = model::detail,
                account = model::account,
                expand = model::expand,
                accountsPage = model::accountsPage,
                recordsPage = model::recordsPage,
                section = model::section,
                payment = model::payment,
                revoke = model::revoke,
                retry = model::retry,
                cancelException = model::cancelException,
                hideAmounts = model::hideAmounts,
                defaultAccount = model::defaultAccount,
                disconnect = model::disconnect,
            )
        }
    BillsSurface(s, actions)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun BillsSurface(s: UiState, actions: UiActions) {
    val homeList = rememberLazyListState()
    val detailsList = rememberLazyListState()
    val settingsList = rememberLazyListState()
    val detailList = rememberLazyListState()
    var bank by rememberSaveable { mutableStateOf("") }
    var holder by rememberSaveable { mutableStateOf("") }
    var mode by rememberSaveable { mutableStateOf("current") }
    var filterOpen by remember { mutableStateOf(false) }
    var accountsOpen by remember { mutableStateOf(false) }
    var infoOpen by remember { mutableStateOf(false) }
    var detailsMenuOpen by remember(s.screen) { mutableStateOf(false) }
    var paymentForm by rememberSaveable(s.statement?.id) { mutableStateOf<String?>(null) }
    val nested = s.screen == "detail"
    LaunchedEffect(s.screen, s.selectedAccount?.id) {
        if (s.screen == "details") detailsList.scrollToItem(0)
    }
    LaunchedEffect(s.connection) {
        if (s.connection == null) {
            accountsOpen = false
            filterOpen = false
            infoOpen = false
            detailsMenuOpen = false
            paymentForm = null
        }
    }
    BackHandler(enabled = s.connection != null && nested) { if (!s.busy) actions.back() }
    BillsTheme {
        if (s.connection == null) PairScreen(s, actions.pair)
        else {
            Scaffold(
                containerColor = Color.White,
                topBar = {
                    if (s.screen == "details" || nested) {
                        Column(Modifier.background(Header).statusBarsPadding()) {
                            Row(
                                Modifier.fillMaxWidth()
                                    .heightIn(min = 64.dp)
                                    .padding(horizontal = 8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                if (nested)
                                    IconButton(
                                        onClick = actions.back,
                                        enabled = !s.busy,
                                        modifier = Modifier.testTag("back"),
                                    ) {
                                        AppIcon("back", Color.White)
                                    }
                                else Spacer(Modifier.width(40.dp))
                                Column(
                                    Modifier.weight(1f)
                                        .clickable(enabled = !s.busy && !nested) {
                                            accountsOpen = true
                                        }
                                        .padding(vertical = 8.dp),
                                    horizontalAlignment = Alignment.CenterHorizontally,
                                ) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(
                                            (if (nested) s.statement?.account
                                                else s.selectedAccount)
                                                ?.bankName ?: "选择还款账户",
                                            color = Color.White,
                                            fontSize = 20.sp,
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis,
                                            modifier = Modifier.weight(1f, false),
                                        )
                                        if (!nested)
                                            AppIcon("down", Color.White, Modifier.size(20.dp))
                                    }
                                    val account =
                                        if (nested) s.statement?.account else s.selectedAccount
                                    Text(
                                        account?.let { "${it.holderName} · ${accountTails(it)}" }
                                            ?: "按账户查看历史账单",
                                        color = Color.White.copy(alpha = .8f),
                                        fontSize = 13.sp,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                                Box {
                                    IconButton(
                                        onClick = {
                                            if (nested) infoOpen = true else detailsMenuOpen = true
                                        },
                                        enabled = !s.busy,
                                        modifier = Modifier.testTag("details-more"),
                                    ) {
                                        AppIcon("more", Color.White)
                                    }
                                    DropdownMenu(detailsMenuOpen, { detailsMenuOpen = false }) {
                                        DropdownMenuItem(
                                            text = { Text("选择还款账户") },
                                            onClick = {
                                                detailsMenuOpen = false
                                                accountsOpen = true
                                            },
                                        )
                                        val selectedStatement =
                                            s.statement?.takeIf {
                                                it.id == s.expandedStatementId &&
                                                    it.account.id == s.selectedAccount?.id
                                            }
                                        if (selectedStatement != null) {
                                            DropdownMenuItem(
                                                text = { Text("账单信息") },
                                                onClick = {
                                                    detailsMenuOpen = false
                                                    infoOpen = true
                                                },
                                            )
                                            DropdownMenuItem(
                                                text = { Text("账单详情与还款") },
                                                modifier = Modifier.testTag("open-statement"),
                                                onClick = {
                                                    detailsMenuOpen = false
                                                    actions.detail(selectedStatement)
                                                },
                                            )
                                        }
                                    }
                                }
                            }
                        }
                    } else {
                        Row(
                            Modifier.fillMaxWidth()
                                .statusBarsPadding()
                                .heightIn(min = 56.dp)
                                .padding(horizontal = 16.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                if (s.screen == "settings") "设置" else "信用卡",
                                fontSize = 24.sp,
                                fontWeight = FontWeight.Bold,
                                modifier = Modifier.weight(1f),
                            )
                            if (s.screen == "home") {
                                IconButton(
                                    onClick = { actions.hideAmounts(!s.amountsHidden) },
                                    modifier = Modifier.testTag("toggle-amounts"),
                                ) {
                                    AppIcon("eye", Muted)
                                }
                                TextButton(onClick = { filterOpen = true }, enabled = !s.busy) {
                                    AppIcon("filter", Ink, Modifier.size(18.dp))
                                    Spacer(Modifier.width(5.dp))
                                    Text("筛选", color = Ink)
                                }
                            } else
                                IconButton(onClick = actions.refresh, enabled = !s.busy) {
                                    AppIcon("refresh", Muted)
                                }
                        }
                    }
                },
                bottomBar = {
                    if (nested)
                        PaymentFooter(s, { paymentForm = "plan" }, { paymentForm = "payment" })
                    else BottomNavigation(s.screen, !s.busy, actions.tab)
                },
            ) { padding ->
                PullToRefreshBox(
                    isRefreshing = s.busy,
                    onRefresh = actions.refresh,
                    modifier = Modifier.fillMaxSize().padding(padding),
                ) {
                    val list =
                        when (s.screen) {
                            "details" -> detailsList
                            "detail" -> detailList
                            "settings" -> settingsList
                            else -> homeList
                        }
                    LazyColumn(
                        state = list,
                        modifier =
                            Modifier.fillMaxSize().imePadding().testTag("screen-${s.screen}"),
                        contentPadding = PaddingValues(bottom = 8.dp),
                    ) {
                        if (s.screen != "details" && !nested) {
                            item(key = "sync") { SyncLine(s, actions.refresh) }
                        } else if (s.cached) {
                            item(key = "detail-read-state") {
                                Text(
                                    "缓存 · 非最新 · 只读",
                                    color = Coral,
                                    fontSize = 11.sp,
                                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
                                )
                            }
                        }
                        s.message?.let {
                            item(key = "message") {
                                Notice(
                                    it,
                                    Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
                                        .testTag("notice"),
                                )
                            }
                        }
                        s.pending?.let { pending ->
                            item(key = "pending") {
                                Box(Modifier.padding(16.dp)) {
                                    PendingCard(
                                        pending,
                                        s.busy,
                                        { actions.retry(false) },
                                        { actions.retry(true) },
                                        actions.cancelException,
                                    )
                                }
                            }
                        }
                        when (s.screen) {
                            "home" -> homeContent(s, actions, bank, holder, mode)
                            "details" -> historyContent(s, actions, { accountsOpen = true })
                            "detail" -> statementContent(s, actions)
                            "settings" ->
                                item(key = "settings") {
                                    SettingsScreen(s, actions, { accountsOpen = true })
                                }
                        }
                    }
                }
            }
        }
        if (filterOpen)
            HomeFilterDialog(
                s,
                bank,
                holder,
                mode,
                { bank = it },
                { holder = it },
                { mode = it },
                { filterOpen = false },
            )
        if (accountsOpen)
            AccountPicker(
                s,
                actions,
                { accountsOpen = false },
                defaultMode = s.screen == "settings",
            )
        if (infoOpen) s.statement?.let { StatementInfo(it, s.amountsHidden, { infoOpen = false }) }
        paymentForm?.let { kind ->
            s.statement?.let { row ->
                PaymentDialog(row.currency, kind == "plan", s.busy, { paymentForm = null }) {
                    amount,
                    time,
                    note ->
                    actions.payment(kind == "plan", amount, time, note)
                    paymentForm = null
                }
            }
        }
    }
}

@Composable
private fun BottomNavigation(route: String, enabled: Boolean, select: (String) -> Unit) {
    Column(Modifier.background(Color.White)) {
        HorizontalDivider(color = Line)
        Row(Modifier.fillMaxWidth().navigationBarsPadding().heightIn(min = 56.dp)) {
            listOf(
                    Triple("home", "账单", "bills"),
                    Triple("details", "详单", "details"),
                    Triple("settings", "设置", "settings"),
                )
                .forEach { (id, label, icon) ->
                    val selected = route == id
                    Column(
                        Modifier.weight(1f)
                            .clickable(enabled = enabled, onClickLabel = label) { select(id) }
                            .testTag("nav-$id")
                            .heightIn(min = 48.dp)
                            .padding(vertical = 6.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(3.dp),
                    ) {
                        AppIcon(
                            icon,
                            if (selected) Ink else Color(0xFFCDCDD2),
                            Modifier.size(23.dp),
                        )
                        Text(label, color = if (selected) Ink else Muted, fontSize = 12.sp)
                    }
                }
        }
    }
}

@Composable
private fun SyncLine(s: UiState, refresh: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            .testTag("sync-line")
            .padding(start = 16.dp, end = 8.dp)
            .heightIn(min = 38.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            if (s.cached) "缓存 · 非最新 · 只读  ${stamp(s.cacheAt)}" else "手机同步 ${stamp(s.cacheAt)}",
            color = if (s.cached) Coral else Muted,
            fontSize = 11.sp,
            modifier = Modifier.weight(1f),
        )
        TextButton(
            onClick = refresh,
            enabled = !s.busy,
            contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
        ) {
            Text(if (s.busy) "读取中…" else "刷新", fontSize = 12.sp)
        }
    }
}
