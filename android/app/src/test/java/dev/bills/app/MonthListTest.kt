package dev.bills.app

import java.io.IOException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class MonthListTest {
    private val account = Account("a", "账户", "测试银行", "测试持卡人", "merged", true, listOf("1234"))
    private val bill =
        Statement(
            "s",
            account,
            "2026-10-09",
            "CNY",
            "confirmed",
            1,
            "100.00",
            "10.00",
            "2026-10-15",
            "0.00",
            "100.00",
            "unpaid",
            1,
            false,
            "complete",
            "2026-09-10",
            "2026-10-09",
            "manual",
        )

    private fun page(number: Int, total: Int, count: Int, size: Int = 20) =
        Page(List(count) { bill.copy(id = "s${(number - 1) * size + it}") }, total, number, size)

    private fun collect(pages: List<Page<Statement>>): List<Loaded<Page<Statement>>> {
        val published = mutableListOf<Loaded<Page<Statement>>>()
        runBlocking {
            readAllStatementMonths(
                "a",
                { Loaded(pages[it - 1], 100L, false) },
                { published.add(it) },
            )
        }
        return published
    }

    @Test
    fun readsAllPagesInServerOrderAndPublishesImmutablePrefixes() {
        val pages = listOf(page(1, 45, 20), page(2, 45, 20), page(3, 45, 5))
        val published = collect(pages)
        assertEquals(listOf(20, 40, 45), published.map { it.data.items.size })
        assertEquals(pages.flatMap { it.items }, published.last().data.items)
        assertEquals(45, published.last().data.total)
        assertFalse(published.last().data.hasNext)
    }

    @Test
    fun emptyAccountStopsAfterOneRead() {
        val reads = mutableListOf<Int>()
        runBlocking {
            readAllStatementMonths(
                "a",
                {
                    reads.add(it)
                    Loaded(page(it, 0, 0), 1L, false)
                },
                {
                    assertTrue(it.data.items.isEmpty())
                    assertEquals(0, it.data.total)
                    assertEquals(1, it.data.size)
                },
            )
        }
        assertEquals(listOf(1), reads)
    }

    @Test
    fun shortHistoryDoesNotRequestAnotherPage() {
        val reads = mutableListOf<Int>()
        runBlocking {
            readAllStatementMonths(
                "a",
                {
                    reads.add(it)
                    Loaded(page(it, 2, 2), 1L, false)
                },
                { assertEquals(2, it.data.items.size) },
            )
        }
        assertEquals(listOf(1), reads)
    }

    @Test
    fun mixedCacheHistoryUsesOldestTimeAndStaysReadOnly() {
        val published = mutableListOf<Loaded<Page<Statement>>>()
        val pages =
            listOf(
                Loaded(page(1, 45, 20), 300L, false),
                Loaded(page(2, 45, 20), 100L, true),
                Loaded(page(3, 45, 5), 400L, false),
            )
        runBlocking { readAllStatementMonths("a", { pages[it - 1] }, { published.add(it) }) }
        assertFalse(published.first().cached)
        assertTrue(published.last().cached)
        assertEquals(100L, published.last().at)
        val state =
            UiState(busy = false).openAccountHistory(account).withHistoryPage("a", published.last())
        assertTrue(state.historyCached)
        assertFalse(canWrite(state))
        assertNull(state.expandedStatementId)
    }

    @Test
    fun fullyCachedHistoryStillIncludesLaterMonths() {
        var last: Loaded<Page<Statement>>? = null
        runBlocking {
            readAllStatementMonths(
                "a",
                { Loaded(page(it, 25, if (it == 1) 20 else 5), 10L, true) },
                { last = it },
            )
        }
        assertEquals(25, last!!.data.items.size)
        assertTrue(last!!.cached)
    }

    @Test
    fun sameMonthDifferentCurrenciesAndVersionsRemainSeparate() {
        val rows = listOf(bill, bill.copy(id = "usd", currency = "USD", version = 7))
        val published = collect(listOf(Page(rows, 2, 1, 20)))
        assertEquals(rows, published.single().data.items)
        assertEquals(listOf("CNY", "USD"), published.single().data.items.map { it.currency })
        assertEquals(listOf(1, 7), published.single().data.items.map { it.version })
    }

    @Test
    fun failedLaterPageKeepsOnlyValidatedPrefix() {
        val published = mutableListOf<Loaded<Page<Statement>>>()
        assertThrows(IOException::class.java) {
            runBlocking {
                readAllStatementMonths(
                    "a",
                    {
                        if (it == 1) Loaded(page(1, 25, 20), 1L, false)
                        else throw IOException("offline")
                    },
                    { published.add(it) },
                )
            }
        }
        assertEquals(20, published.single().data.items.size)
        assertEquals(25, published.single().data.total)
    }

    @Test
    fun malformedOrForeignAccountPageIsNeverPublished() {
        val invalid =
            listOf(
                page(1, 20, 20).copy(page = 2),
                page(1, 20, 20).copy(size = 0),
                page(1, 20, 20).copy(total = -1),
                page(1, 20, 19),
                page(1, 0, 1),
                Page(listOf(bill.copy(account = account.copy(id = "other"))), 1, 1, 20),
                Page(listOf(bill, bill), 2, 1, 20),
            )
        invalid.forEach { page ->
            val published = mutableListOf<Loaded<Page<Statement>>>()
            assertThrows(IllegalStateException::class.java) {
                runBlocking {
                    readAllStatementMonths("a", { Loaded(page, 1L, false) }, { published.add(it) })
                }
            }
            assertTrue(published.isEmpty())
        }
    }

    @Test
    fun overlappingPagesNeverSilentlyDropOrDuplicateAStatement() {
        val published = mutableListOf<Loaded<Page<Statement>>>()
        assertThrows(IllegalStateException::class.java) {
            runBlocking {
                readAllStatementMonths(
                    "a",
                    {
                        val data =
                            if (it == 1) page(1, 25, 20)
                            else
                                page(2, 25, 5).let { p ->
                                    p.copy(items = p.items.map { row -> row.copy(id = "s0") })
                                }
                        Loaded(data, 1L, false)
                    },
                    { published.add(it) },
                )
            }
        }
        assertEquals(20, published.single().data.items.size)
    }

    @Test
    fun changedTotalsOrPageSizesStopBeforePublishingInvalidPage() {
        listOf(page(2, 26, 6), page(2, 25, 5, size = 10)).forEach { second ->
            val published = mutableListOf<Loaded<Page<Statement>>>()
            assertThrows(IllegalStateException::class.java) {
                runBlocking {
                    readAllStatementMonths(
                        "a",
                        { Loaded(if (it == 1) page(1, 25, 20) else second, 1L, false) },
                        { published.add(it) },
                    )
                }
            }
            assertEquals(20, published.single().data.items.size)
        }
    }
}
