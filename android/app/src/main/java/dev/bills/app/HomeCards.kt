package dev.bills.app

// Cardless legacy accounts remain accessible in Details, but never get a home bill card.
internal fun hasHomeBillCard(account: Account): Boolean =
    account.active && account.tails.isNotEmpty() && account.hasTrackedCard

internal fun isHomeAccountVisible(s: UiState, account: Account): Boolean {
    val current = (s.homeAccounts ?: s.accounts?.items.orEmpty()).find { it.id == account.id }
    return hasHomeBillCard(account) && (current == null || hasHomeBillCard(current))
}

internal fun homeStatementRows(
    s: UiState,
    bank: String = "",
    holder: String = "",
    mode: String = "current",
): List<Statement> =
    (if (mode == "history") s.home?.historical.orEmpty() else s.home?.latest.orEmpty())
        .filter {
            isHomeAccountVisible(s, it.account) &&
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

internal fun missingHomeAccounts(
    s: UiState,
    bank: String = "",
    holder: String = "",
    mode: String = "current",
): List<Account> =
    if (mode == "current" && s.home != null)
        (s.homeAccounts ?: s.accounts?.items.orEmpty()).filter { a ->
            hasHomeBillCard(a) &&
                s.home.latest.none { it.account.id == a.id } &&
                (bank.isEmpty() || a.bankName == bank) &&
                (holder.isEmpty() || a.holderName == holder)
        }
    else emptyList()

// Read the complete inventory, not only the first 20 accounts. Publish validated
// prefixes on failure and preserve cache provenance so partial/offline reads stay read-only.
internal suspend fun readAllHomeAccounts(
    read: suspend (Int) -> Loaded<Page<Account>>,
    publish: (Loaded<List<Account>>) -> Unit,
) {
    val items = mutableListOf<Account>()
    val ids = mutableSetOf<String>()
    var first: Page<Account>? = null
    var number = 1
    var cached = false
    var oldest: Long? = null
    while (true) {
        val loaded = read(number)
        val page = loaded.data
        check(page.page == number && page.size > 0 && page.total >= 0) { "账户分页信息不一致，请下拉重试" }
        val expected = first
        check(expected == null || page.total == expected.total && page.size == expected.size) {
            "账户列表读取期间发生变化，请下拉重试"
        }
        val remaining = page.total - items.size
        check(remaining >= 0 && page.items.size == minOf(page.size, remaining)) {
            "账户列表未完整读取，请下拉重试"
        }
        val pageIds = page.items.map { it.id }
        check(pageIds.toSet().size == pageIds.size && pageIds.none { it in ids }) {
            "账户分页发生重叠，请下拉重试"
        }
        if (first == null) first = page
        items.addAll(page.items)
        ids.addAll(pageIds)
        cached = cached || loaded.cached
        val at = oldest?.let { minOf(it, loaded.at) } ?: loaded.at
        oldest = at
        publish(Loaded(items.toList(), at, cached))
        if (items.size == page.total) return
        number++
    }
}
