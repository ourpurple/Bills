package dev.bills.app

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONObject

// All user operations are serialized by the single busy gate; a connection cannot
// switch while a read/write is in flight. No service performs background writes.
data class UiState(
    val connection: Connection? = null,
    val screen: String = "home",
    val busy: Boolean = true,
    val message: String? = null,
    val cached: Boolean = false,
    val cacheAt: Long? = null,
    val home: Overview? = null,
    val accounts: Page<Account>? = null,
    val selectedAccount: Account? = null,
    val history: Page<Statement>? = null,
    val statement: Statement? = null,
    val transactions: Page<Transaction>? = null,
    val transactionsIncomplete: Boolean = false,
    val repayments: Page<PaymentRecord>? = null,
    val plans: Page<PaymentRecord>? = null,
    val pending: Pending? = null,
    val detailReturn: String = "home",
    val detailSection: String = "transactions",
    val expandedStatementId: String? = null,
    val historyAccountId: String? = null,
    val historyCached: Boolean = true,
    val historyAt: Long? = null,
    val amountsHidden: Boolean = false,
    val defaultAccountId: String? = null,
)

class BillsViewModel(application: Application) : AndroidViewModel(application) {
    private val db = CacheDb.open(application)
    private val repo = Repository(Vault(application), db)
    private val preferences =
        application.getSharedPreferences(
            "display-preferences",
            android.content.Context.MODE_PRIVATE,
        )

    private fun initialState() =
        UiState(
            amountsHidden = preferences.getBoolean("amounts-hidden", false),
            defaultAccountId = preferences.getString("default-account", null),
        )

    private val mutable = MutableStateFlow(initialState())
    val state = mutable.asStateFlow()

    init {
        viewModelScope.launch {
            try {
                val reset = repo.restore()
                connection()
                if (reset) mutable.update { it.copy(message = "本机授权已到期或安全存储不可用，已清理缓存，请重新配对") }
                if (repo.session != null) syncHome()
            } catch (e: Exception) {
                failure(e)
            } finally {
                mutable.update { it.copy(busy = false, pending = repo.pending) }
            }
        }
    }

    private fun connection() {
        mutable.update {
            it.copy(
                connection = repo.session?.let { s -> Connection(s.server, s.name, s.expiresAt) },
                pending = repo.pending,
            )
        }
    }

    private fun task(block: suspend () -> Unit) {
        if (mutable.value.busy) return
        mutable.update { it.copy(busy = true, message = null) }
        viewModelScope.launch {
            try {
                block()
            } catch (e: Exception) {
                failure(e)
            } finally {
                mutable.update {
                    it.rememberHistoryRead().copy(busy = false, pending = repo.pending)
                }
            }
        }
    }

    private fun failure(e: Exception) {
        if (repo.session == null && mutable.value.connection != null) {
            mutable.value = initialState().copy(busy = true, message = "设备授权已失效，本机凭证和缓存已清理，请重新配对")
            return
        }
        val text =
            when (e) {
                is ApiError -> e.message
                is IllegalArgumentException -> e.message ?: "请检查输入"
                else ->
                    if (repo.pending != null) "未收到提交确认，可能已记录。请保持内容不变，点击核对重试；不要另建一条。"
                    else "无法读取服务，且此页没有可用缓存。请检查网络后重试。"
            }
        mutable.update {
            it.copy(
                message = text,
                cached = it.cached || e !is ApiError && e !is IllegalArgumentException,
            )
        }
    }

    private fun <T> observed(loaded: Loaded<T>, reset: Boolean = false) {
        mutable.update {
            it.copy(
                cached = if (reset) loaded.cached else it.cached || loaded.cached,
                cacheAt =
                    if (reset || it.cacheAt == null) loaded.at else minOf(it.cacheAt, loaded.at),
            )
        }
    }

    fun pair(server: String, code: String, name: String) = task {
        repo.pair(Rules.server(server, BuildConfig.DEBUG), code.trim(), name.trim())
        connection()
        mutable.update { it.copy(screen = "home") }
        syncHome()
    }

    private suspend fun syncHome() {
        val home = repo.read("/api/v1/app/overview", Json::overview)
        observed(home, true)
        mutable.update { it.copy(home = home.data) }
        val accounts =
            repo.read("/api/v1/app/accounts?page=1&page_size=20") { Json.page(it, Json::account) }
        observed(accounts)
        mutable.update { it.copy(accounts = accounts.data) }
    }

    fun home() = tab("home")

    fun tab(name: String) = task {
        require(name in listOf("home", "details", "settings"))
        mutable.update { it.copy(screen = name, message = null) }
        if (name == "home") syncHome()
        if (name == "details") {
            val state = mutable.value
            val account = state.selectedAccount ?: findInitialAccount()
            if (
                account != null && (state.history == null || state.historyAccountId != account.id)
            ) {
                mutable.update { it.copy(selectedAccount = account) }
                loadHistory()
            } else {
                mutable.update { it.copy(cached = it.historyCached, cacheAt = it.historyAt) }
            }
        }
    }

    private suspend fun findInitialAccount(): Account? {
        val state = mutable.value
        val fallback =
            state.accounts?.items?.firstOrNull { it.active } ?: state.accounts?.items?.firstOrNull()
        val id = state.defaultAccountId ?: return fallback
        state.accounts
            ?.items
            ?.find { it.id == id }
            ?.let {
                return it
            }
        (state.home?.latest.orEmpty() + state.home?.historical.orEmpty())
            .find { it.account.id == id }
            ?.let {
                return it.account
            }
        // Locate a saved account outside the first page without fetching any transaction history.
        var page = state.accounts
        while (page?.hasNext == true) {
            val next = page.page + 1
            val result =
                repo.read("/api/v1/app/accounts?page=$next&page_size=20") {
                    Json.page(it, Json::account)
                }
            observed(result)
            val data = result.data
            page = data
            data.items
                .find { it.id == id }
                ?.let {
                    mutable.update { state -> state.copy(accounts = data) }
                    return it
                }
        }
        mutable.update { it.copy(message = "默认账户未在当前列表找到，已选择可用账户；可在设置中重新选择。") }
        return fallback
    }

    fun section(name: String) {
        if (!mutable.value.busy && name in listOf("transactions", "repayments", "plans"))
            mutable.update { it.copy(detailSection = name) }
    }

    fun hideAmounts(hidden: Boolean) {
        preferences.edit().putBoolean("amounts-hidden", hidden).apply()
        mutable.update { it.copy(amountsHidden = hidden) }
    }

    fun defaultAccount(id: String?) {
        preferences.edit().putString("default-account", id).apply()
        mutable.update { it.copy(defaultAccountId = id) }
    }

    fun refresh() = task {
        when (mutable.value.screen) {
            "detail" -> loadDetail(mutable.value.statement?.id ?: return@task)
            "details" -> {
                if (mutable.value.selectedAccount != null) loadHistory()
                else {
                    loadAccounts(1)
                }
            }
            else -> syncHome()
        }
    }

    private suspend fun loadAccounts(page: Int) {
        mutable.update { it.copy(accounts = null) }
        val result =
            repo.read("/api/v1/app/accounts?page=$page&page_size=20") {
                Json.page(it, Json::account)
            }
        // Paging the picker must not turn a cached statement into an online/writeable one.
        observed(result, mutable.value.screen !in listOf("details", "detail"))
        mutable.update { it.copy(accounts = result.data) }
    }

    fun accountsPage(page: Int) = task { loadAccounts(page) }

    fun account(row: Account) = task {
        mutable.update { it.openAccountHistory(row) }
        loadHistory()
    }

    private suspend fun loadHistory() {
        val id = mutable.value.selectedAccount?.id ?: return
        mutable.update {
            it.copy(
                history = null,
                statement = null,
                transactions = null,
                repayments = null,
                plans = null,
                expandedStatementId = null,
            )
        }
        // Keep the API/cache paged, but show every month without paging controls in Android.
        // Transaction details are still read only when the user expands a month.
        readAllStatementMonths(
            accountId = id,
            read = { page ->
                repo.read("/api/v1/app/statements?account_id=$id&page=$page&page_size=20") {
                    Json.page(it, Json::statement)
                }
            },
            publish = { result -> mutable.update { it.withHistoryPage(id, result) } },
        )
    }

    fun detail(row: Statement) = task {
        mutable.update {
            it.copy(
                screen = "detail",
                detailReturn = if (it.screen == "details") "details" else "home",
                selectedAccount = row.account,
                expandedStatementId = row.id,
                statement = row,
                transactions = null,
                repayments = null,
                plans = null,
                cached = true,
            )
        }
        loadDetail(row.id)
    }

    fun expand(row: Statement) = task {
        if (mutable.value.expandedStatementId == row.id) {
            mutable.update { it.copy(expandedStatementId = null) }
        } else {
            mutable.update {
                it.copy(
                    statement = row,
                    expandedStatementId = row.id,
                    cached = true,
                    transactions = null,
                    repayments = null,
                    plans = null,
                )
            }
            loadDetail(row.id)
        }
    }

    private suspend fun loadDetail(id: String) {
        val summary =
            repo.read("/api/v1/app/statements/$id/summary") { Json.statement(JSONObject(it)) }
        observed(summary, true)
        mutable.update {
            it.copy(
                statement = summary.data,
                transactions = null,
                repayments = null,
                plans = null,
                history =
                    it.history?.let { page ->
                        page.copy(
                            items =
                                page.items.map { row -> if (row.id == id) summary.data else row }
                        )
                    },
                cached = it.cached || (it.screen == "details" && it.historyCached),
                cacheAt =
                    if (it.screen == "details") listOfNotNull(it.cacheAt, it.historyAt).minOrNull()
                    else it.cacheAt,
            )
        }
        val reads: List<suspend () -> Unit> =
            listOf({ loadTransactions() }, { loadRecords(false, 1) }, { loadRecords(true, 1) })
        for (read in reads) try {
            read()
        } catch (e: Exception) {
            if (repo.session == null) throw e
            failure(e)
        }
        if (mutable.value.screen == "details")
            mutable.update { it.copy(historyCached = it.cached, historyAt = it.cacheAt) }
    }

    private suspend fun loadTransactions() {
        val s = mutable.value.statement ?: return
        mutable.update { it.copy(transactions = null, transactionsIncomplete = true) }
        readAllTransactions(
            version = s.version,
            read = { page ->
                repo.read(
                    "/api/v1/app/statements/${s.id}/transactions?version=${s.version}&page=$page&page_size=20"
                ) {
                    Json.page(it, Json::transaction)
                }
            },
            publish = { result ->
                observed(result)
                mutable.update {
                    it.copy(
                        transactions = result.data,
                        transactionsIncomplete = result.data.items.size != result.data.total,
                    )
                }
            },
        )
    }

    private suspend fun loadRecords(plan: Boolean, page: Int) {
        val s = mutable.value.statement ?: return
        mutable.update { if (plan) it.copy(plans = null) else it.copy(repayments = null) }
        val kind = if (plan) "plans" else "repayments"
        val result =
            repo.read("/api/v1/app/statements/${s.id}/$kind?page=$page&page_size=20") {
                Json.page(it) { o -> Json.payment(o, plan) }
            }
        observed(result)
        mutable.update {
            if (plan) it.copy(plans = result.data) else it.copy(repayments = result.data)
        }
    }

    fun recordsPage(plan: Boolean, page: Int) = task { loadRecords(plan, page) }

    fun back() = task {
        val target = mutable.value.detailReturn
        mutable.update { it.copy(screen = target, message = null) }
        if (target == "home") syncHome()
        else
            mutable.update {
                it.copy(
                    cached = it.cached || it.historyCached,
                    cacheAt = listOfNotNull(it.cacheAt, it.historyAt).minOrNull(),
                    historyCached = it.cached || it.historyCached,
                    historyAt = listOfNotNull(it.cacheAt, it.historyAt).minOrNull(),
                )
            }
    }

    private suspend fun afterWrite() {
        // Server receipt succeeded. Subsequent read failure must not imply write failure.
        val id = mutable.value.statement?.id
        mutable.update {
            it.copy(message = "服务端已确认记录；仅为用户标记，不代表银行到账。计划不减少待还，也尚无通知投递。", cached = true)
        }
        try {
            if (id != null) loadDetail(id)
            val h = repo.read("/api/v1/app/overview", Json::overview)
            mutable.update { it.copy(home = h.data, cached = it.cached || h.cached) }
        } catch (e: Exception) {
            if (repo.session == null) failure(e)
            else
                mutable.update {
                    it.copy(message = "服务端已确认记录，但刷新失败。当前金额不是最新，请联网刷新。", cached = true)
                }
        }
    }

    fun payment(plan: Boolean, amount: String, time: String, note: String) = task {
        check(!mutable.value.cached) { "缓存只读，请先联网刷新" }
        val s = mutable.value.statement ?: return@task
        require(s.status == "confirmed" && s.account.active) { "待确认、已作废或停用账户请到后台处理" }
        require(note.length <= 500) { "备注最多500字" }
        val body =
            JSONObject()
                .put("amount", Rules.amount(amount, s.currency))
                .put("currency", s.currency)
                .put("occurred_at", Rules.instant(time))
                .put("note", note)
                .put("confirm_exception", false)
                .toString()
        repo.stage(
            "/api/v1/app/statements/${s.id}/${if(plan) "plans" else "repayments"}",
            body,
            if (plan) "计划还款" else "记录还款",
        )
        repo.submit()
        afterWrite()
    }

    fun revoke(plan: Boolean, id: String, reason: String) = task {
        check(!mutable.value.cached) { "缓存只读，请先联网刷新" }
        require(reason.trim().length in 1..500) { "请填写纠错原因（最多500字）" }
        repo.stage(
            "/api/v1/app/${if(plan) "plans" else "repayments"}/$id/${if(plan) "cancel" else "revoke"}",
            JSONObject().put("reason", reason.trim()).toString(),
            if (plan) "取消计划" else "撤销还款标记",
        )
        repo.submit()
        afterWrite()
    }

    fun retry(confirm: Boolean = false) = task {
        if (confirm) repo.confirm()
        repo.submit()
        afterWrite()
    }

    fun cancelException() = task { repo.abandonRejected() }

    fun disconnect() = task {
        val revoked = repo.disconnect()
        mutable.value =
            initialState()
                .copy(
                    busy = true,
                    message =
                        if (revoked) "设备已断开并撤销授权，本机缓存已清理" else "本机凭证和缓存已清理；未能确认远端撤销，请到后台安全设置撤销此设备",
                )
    }

    override fun onCleared() {
        db.close()
    }
}
