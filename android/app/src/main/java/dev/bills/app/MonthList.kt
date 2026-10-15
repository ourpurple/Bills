package dev.bills.app

// Only month summaries are aggregated. Keep account, currency and statement version boundaries.
// Never silently deduplicate overlapping pages, which would hide a changing/incomplete history.
internal suspend fun readAllStatementMonths(
    accountId: String,
    read: suspend (Int) -> Loaded<Page<Statement>>,
    publish: (Loaded<Page<Statement>>) -> Unit,
) {
    val items = mutableListOf<Statement>()
    val ids = mutableSetOf<String>()
    var first: Page<Statement>? = null
    var number = 1
    var cached = false
    var oldest: Long? = null
    while (true) {
        val loaded = read(number)
        val page = loaded.data
        check(page.page == number && page.size > 0 && page.total >= 0) { "月份账单分页信息不一致，请下拉重试" }
        val expected = first
        check(expected == null || page.total == expected.total && page.size == expected.size) {
            "月份账单读取期间发生变化，请下拉重试"
        }
        val remaining = page.total - items.size
        check(remaining >= 0 && page.items.size == minOf(page.size, remaining)) {
            "月份账单未完整读取，请下拉重试"
        }
        check(page.items.all { it.account.id == accountId }) { "月份账单账户不一致，请下拉重试" }
        val pageIds = page.items.map { it.id }
        check(pageIds.toSet().size == pageIds.size && pageIds.none { it in ids }) {
            "月份账单分页发生重叠，请下拉重试"
        }
        if (first == null) first = page
        items.addAll(page.items)
        ids.addAll(pageIds)
        cached = cached || loaded.cached
        val at = oldest?.let { minOf(it, loaded.at) } ?: loaded.at
        oldest = at
        publish(
            Loaded(
                page.copy(items = items.toList(), page = 1, size = maxOf(page.total, 1)),
                at,
                cached,
            )
        )
        if (items.size == page.total) return
        number++
    }
}
