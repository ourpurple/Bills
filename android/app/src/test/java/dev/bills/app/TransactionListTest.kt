package dev.bills.app

import java.io.IOException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class TransactionListTest {
    private val transaction =
        Transaction("重复交易", "2026-10-01", null, "3.97", "CNY", "debit", "9512")

    private fun page(number: Int, total: Int, count: Int, size: Int = 20) =
        Page(List(count) { transaction }, total, number, size, 3, "complete")

    private fun collect(pages: List<Page<Transaction>>): List<Loaded<Page<Transaction>>> {
        val published = mutableListOf<Loaded<Page<Transaction>>>()
        runBlocking {
            readAllTransactions(3, { Loaded(pages[it - 1], 100L, false) }, { published.add(it) })
        }
        return published
    }

    @Test
    fun loadsEveryPageInOrderWithoutRemovingDuplicates() {
        val final = transaction.copy(description = "末页交易")
        val pages =
            listOf(page(1, 45, 20), page(2, 45, 20), page(3, 45, 5).copy(items = List(5) { final }))
        val published = collect(pages)
        assertEquals(listOf(20, 40, 45), published.map { it.data.items.size })
        assertEquals(List(40) { transaction } + List(5) { final }, published.last().data.items)
        assertEquals(45, published.last().data.total)
        assertFalse(published.last().data.hasNext)
    }

    @Test
    fun shortStatementNeedsOnlyOneRequest() {
        val results = collect(listOf(page(1, 4, 4)))
        assertEquals(1, results.size)
        assertEquals(4, results.single().data.items.size)
    }

    @Test
    fun zeroTransactionsAreACompleteEmptyList() {
        val result = collect(listOf(page(1, 0, 0))).single().data
        assertTrue(result.items.isEmpty())
        assertEquals(0, result.total)
        assertFalse(result.hasNext)
    }

    @Test
    fun exactlyFullLastPageDoesNotRequestAnExtraPage() {
        assertEquals(40, collect(listOf(page(1, 40, 20), page(2, 40, 20))).last().data.items.size)
    }

    @Test
    fun cachedPagesKeepMergedReadOnlyAndUseOldestTimestamp() {
        val published = mutableListOf<Loaded<Page<Transaction>>>()
        val results =
            listOf(
                Loaded(page(1, 45, 20), 300L, false),
                Loaded(page(2, 45, 20), 100L, true),
                Loaded(page(3, 45, 5), 400L, false),
            )
        runBlocking { readAllTransactions(3, { results[it - 1] }, { published.add(it) }) }
        assertFalse(published.first().cached)
        assertTrue(published.last().cached)
        assertEquals(100L, published.last().at)
        assertEquals(45, published.last().data.items.size)
    }

    @Test
    fun fullyCachedStatementStillLoadsAllAvailableRows() {
        val published = mutableListOf<Loaded<Page<Transaction>>>()
        runBlocking {
            readAllTransactions(
                3,
                { Loaded(page(it, 25, if (it == 1) 20 else 5), 10L, true) },
                { published.add(it) },
            )
        }
        assertEquals(25, published.last().data.items.size)
        assertTrue(published.last().cached)
    }

    @Test
    fun failedLaterReadKeepsOnlyValidatedPrefixNotACompleteList() {
        val published = mutableListOf<Loaded<Page<Transaction>>>()
        assertThrows(IOException::class.java) {
            runBlocking {
                readAllTransactions(
                    3,
                    {
                        if (it == 1) Loaded(page(1, 25, 20), 10L, false)
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
    fun invalidFirstPageIsNeverPublished() {
        val invalid =
            listOf(
                page(1, 20, 20).copy(version = 4),
                page(1, 20, 20).copy(page = 2),
                page(1, 20, 20).copy(size = 0),
                page(1, 20, 20).copy(total = -1),
                page(1, 20, 19),
                page(1, 0, 1),
            )
        invalid.forEach { page ->
            val published = mutableListOf<Loaded<Page<Transaction>>>()
            assertThrows(IllegalStateException::class.java) {
                runBlocking {
                    readAllTransactions(3, { Loaded(page, 10L, false) }, { published.add(it) })
                }
            }
            assertTrue(published.isEmpty())
        }
    }

    @Test
    fun changedVersionOrPaginationCannotBeMerged() {
        val invalid =
            listOf(
                page(2, 25, 5).copy(version = 4),
                page(2, 25, 5).copy(page = 1),
                page(2, 25, 5).copy(size = 10),
                page(2, 25, 5).copy(total = 26),
                page(2, 25, 5).copy(detailStatus = "partial"),
                page(2, 25, 0),
            )
        invalid.forEach { last ->
            val published = mutableListOf<Loaded<Page<Transaction>>>()
            assertThrows(IllegalStateException::class.java) {
                runBlocking {
                    readAllTransactions(
                        3,
                        { Loaded(if (it == 1) page(1, 25, 20) else last, 10L, false) },
                        { published.add(it) },
                    )
                }
            }
            assertEquals(20, published.single().data.items.size)
        }
    }

    @Test
    fun partialExtractionStatusIsPreservedAfterAllPagesLoad() {
        val result = collect(listOf(page(1, 3, 3).copy(detailStatus = "partial"))).single()
        assertEquals("partial", result.data.detailStatus)
        assertEquals(3, result.data.items.size)
    }
}
