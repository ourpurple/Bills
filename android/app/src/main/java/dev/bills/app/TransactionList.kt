package dev.bills.app

// The API and encrypted cache remain paged. Only the Android presentation is continuous.
// Validate before publishing each prefix; never merge different versions or deduplicate rows.
internal suspend fun readAllTransactions(
    version: Int,
    read: suspend (Int) -> Loaded<Page<Transaction>>,
    publish: (Loaded<Page<Transaction>>) -> Unit,
) {
    val items = mutableListOf<Transaction>()
    var first: Page<Transaction>? = null
    var number = 1
    var cached = false
    var oldest: Long? = null
    while (true) {
        val loaded = read(number)
        val page = loaded.data
        check(page.version == version && page.page == number && page.size > 0 && page.total >= 0) {
            "交易明细版本或分页信息不一致，请刷新重试"
        }
        val expected = first
        if (expected != null) {
            check(
                page.total == expected.total &&
                    page.size == expected.size &&
                    page.detailStatus == expected.detailStatus
            ) {
                "交易明细读取期间发生变化，请刷新重试"
            }
        }
        val remaining = page.total - items.size
        check(remaining >= 0 && page.items.size == minOf(page.size, remaining)) {
            "交易明细未完整读取，请刷新重试"
        }
        if (first == null) first = page
        items.addAll(page.items)
        cached = cached || loaded.cached
        val at = oldest?.let { minOf(it, loaded.at) } ?: loaded.at
        oldest = at
        publish(
            Loaded(
                Page(
                    items.toList(),
                    page.total,
                    1,
                    maxOf(page.total, 1),
                    version,
                    page.detailStatus,
                ),
                at,
                cached,
            )
        )
        if (items.size == page.total) return
        number++
    }
}
