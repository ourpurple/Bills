package dev.bills.app

import java.io.IOException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class HomeCardsTest {
    private val account = Account("a", "日常账户", "中国银行", "测试持卡人", "independent", true, listOf("4853"))
    private val bill =
        Statement(
            "s",
            account,
            "2026-10-12",
            "CNY",
            "confirmed",
            1,
            "100.00",
            "10.00",
            "2026-11-01",
            "0.00",
            "100.00",
            "unpaid",
            5,
            false,
            "complete",
            null,
            null,
            "manual",
        )
    private val overview =
        Overview(emptyList(), emptyList(), emptyList(), emptyList(), 0, "", "2026-10-18")

    private fun state(accounts: List<Account>, latest: List<Statement> = emptyList()) =
        UiState(busy = false, home = overview.copy(latest = latest), homeAccounts = accounts)

    @Test
    fun cardlessInactiveAndUntrackedAccountsNeverGetPlaceholders() {
        val hidden =
            listOf(
                account.copy(id = "no-card", tails = emptyList()),
                account.copy(id = "inactive", active = false),
                account.copy(id = "untracked", hasTrackedCard = false),
            )
        assertTrue(missingHomeAccounts(state(hidden)).isEmpty())
        hidden.forEach { assertFalse(hasHomeBillCard(it)) }
    }

    @Test
    fun newTrackedCardWithoutAnyConfirmedBillGetsPlaceholder() {
        val s = state(listOf(account))
        assertEquals(listOf(account), missingHomeAccounts(s))
        assertTrue(homeStatementRows(s).isEmpty())
        assertTrue(missingHomeAccounts(s, mode = "upcoming").isEmpty())
        assertTrue(missingHomeAccounts(s, mode = "history").isEmpty())
    }

    @Test
    fun existingConfirmedBillIsNotDuplicatedAsPlaceholder() {
        val s = state(listOf(account), listOf(bill, bill.copy(id = "usd", currency = "USD")))
        assertEquals(2, homeStatementRows(s).size)
        assertTrue(missingHomeAccounts(s).isEmpty())
    }

    @Test
    fun hiddenAccountsNeverShowRealCardsInAnyBillMode() {
        for (hidden in
            listOf(
                account.copy(tails = emptyList()),
                account.copy(hasTrackedCard = false),
                account.copy(active = false),
            )) {
            val row = bill.copy(account = hidden)
            val s =
                state(listOf(hidden), listOf(row))
                    .copy(
                        home =
                            overview.copy(
                                latest = listOf(row),
                                historical = listOf(row.copy(id = "old")),
                            )
                    )
            for (mode in listOf("current", "upcoming", "history")) {
                assertTrue(homeStatementRows(s, mode = mode).isEmpty())
                assertTrue(missingHomeAccounts(s, mode = mode).isEmpty())
            }
        }
    }

    @Test
    fun refreshedAccountSwitchHidesOlderOverviewAndBlocksOpenRepaymentForm() {
        val s =
            state(listOf(account.copy(hasTrackedCard = false)), listOf(bill))
                .copy(
                    connection =
                        Connection("https://example.invalid", "test", "2026-12-01T00:00:00+08:00")
                )
        assertTrue(homeStatementRows(s).isEmpty())
        assertNull(homePaymentTarget(s, bill))
        assertFalse(
            sameHomePaymentBill(bill, bill.copy(account = account.copy(hasTrackedCard = false)))
        )
    }

    @Test
    fun cachedAccountCannotReactivateCardlessFreshStatement() {
        val s =
            state(listOf(account), listOf(bill.copy(account = account.copy(tails = emptyList()))))
        assertTrue(homeStatementRows(s).isEmpty())
    }

    @Test
    fun newCardPlaceholdersHonorBankAndHolderFilters() {
        val s = state(listOf(account))
        assertEquals(listOf(account), missingHomeAccounts(s, "中国银行", "测试持卡人"))
        assertTrue(missingHomeAccounts(s, bank = "建设银行").isEmpty())
        assertTrue(missingHomeAccounts(s, holder = "另一持卡人").isEmpty())
        assertTrue(missingHomeAccounts(s.copy(home = null)).isEmpty())
    }

    @Test
    fun staleFlagsDoNotChangeOrderingFiltersOrRepaymentEligibility() {
        val rows =
            listOf(
                bill.copy(id = "later", days = 10, due = "2026-11-06"),
                bill.copy(
                    id = "settled",
                    days = -2,
                    due = "2026-10-25",
                    paymentStatus = "settled",
                    remaining = "0.00",
                ),
                bill.copy(id = "soon", days = 2, due = "2026-10-29", paymentStatus = "partial"),
            )
        val original = state(listOf(account), rows)
        val flagged = rows.map { it.copy(needsUpdate = true, expectedStatementDate = "2026-10-23") }
        val s =
            state(listOf(account), flagged)
                .copy(
                    connection =
                        Connection("https://example.invalid", "test", "2026-12-01T00:00:00+08:00")
                )
        assertEquals(listOf("soon", "later", "settled"), homeStatementRows(s).map { it.id })
        for (mode in listOf("current", "upcoming")) {
            assertEquals(
                homeStatementRows(original, mode = mode).map { it.id },
                homeStatementRows(s, mode = mode).map { it.id },
            )
        }
        assertEquals(listOf("soon"), homeStatementRows(s, mode = "upcoming").map { it.id })
        assertTrue(hasRepayableBalance(flagged.first()))
        assertTrue(canRecordHomePayment(s, flagged.first()))
        assertFalse(canRecordHomePayment(s.copy(cached = true), flagged.first()))
    }

    @Test
    fun fullHomeInventoryIsIndependentOfAccountPickerPage() {
        val s =
            state(listOf(account))
                .copy(
                    accounts =
                        Page(listOf(account.copy(id = "picker", tails = emptyList())), 21, 2, 20)
                )
        assertEquals(listOf(account), missingHomeAccounts(s))
        assertEquals(listOf(account), missingHomeAccounts(s.copy(accounts = null)))
    }

    private fun page(number: Int, total: Int, size: Int = 20): Page<Account> {
        val offset = (number - 1) * size
        return Page(
            List(minOf(size, (total - offset).coerceAtLeast(0))) {
                account.copy(id = "a${offset + it}")
            },
            total,
            number,
            size,
        )
    }

    @Test
    fun readsAllAccountPagesSoNewCardAfterFirstTwentyIsVisible() = runBlocking {
        val published = mutableListOf<Loaded<List<Account>>>()
        readAllHomeAccounts({ Loaded(page(it, 45), 100L, false) }, { published.add(it) })
        assertEquals(listOf(20, 40, 45), published.map { it.data.size })
        assertEquals((0 until 45).map { "a$it" }, published.last().data.map { it.id })
        assertEquals(20, published.first().data.size)
        val accounts = published.last().data.map { it.copy(hasTrackedCard = it.id == "a44") }
        assertEquals(listOf("a44"), missingHomeAccounts(state(accounts)).map { it.id })
    }

    @Test
    fun emptyInventoryStopsAfterFirstRead() = runBlocking {
        val reads = mutableListOf<Int>()
        readAllHomeAccounts(
            {
                reads.add(it)
                Loaded(page(it, 0), 10L, false)
            },
            { assertTrue(it.data.isEmpty()) },
        )
        assertEquals(listOf(1), reads)
    }

    @Test
    fun mixedCacheAccountPagesKeepOldestTimestampAndReadOnlyState() = runBlocking {
        val published = mutableListOf<Loaded<List<Account>>>()
        readAllHomeAccounts(
            { Loaded(page(it, 21), if (it == 1) 100L else 50L, it == 2) },
            { published.add(it) },
        )
        assertFalse(published.first().cached)
        assertTrue(published.last().cached)
        assertEquals(50L, published.last().at)
        assertEquals(21, published.last().data.size)
    }

    @Test
    fun interruptedReadKeepsOnlyValidatedPrefix() {
        val published = mutableListOf<Loaded<List<Account>>>()
        assertThrows(IOException::class.java) {
            runBlocking {
                readAllHomeAccounts(
                    {
                        if (it == 1) Loaded(page(1, 21), 100L, false)
                        else throw IOException("offline")
                    },
                    { published.add(it) },
                )
            }
        }
        assertEquals(20, published.single().data.size)
    }

    @Test
    fun invalidFirstPageNeverPublishesAccounts() {
        val invalid =
            listOf(
                page(1, 1).copy(page = 2),
                page(1, 1).copy(size = 0),
                page(1, 1).copy(total = -1),
                page(1, 2).copy(items = listOf(account)),
                page(1, 0).copy(items = listOf(account)),
                Page(listOf(account, account), 2, 1, 20),
            )
        invalid.forEach { page ->
            var published = false
            assertThrows(IllegalStateException::class.java) {
                runBlocking {
                    readAllHomeAccounts({ Loaded(page, 1L, false) }, { published = true })
                }
            }
            assertFalse(published)
        }
    }

    @Test
    fun overlappingOrChangedAccountPagesAreRejected() {
        val first = page(1, 21)
        val invalid =
            listOf(
                page(2, 21).copy(items = listOf(first.items.first())),
                page(2, 22),
                page(2, 21, 10),
                page(2, 21).copy(page = 3),
            )
        invalid.forEach { second ->
            val published = mutableListOf<Loaded<List<Account>>>()
            assertThrows(IllegalStateException::class.java) {
                runBlocking {
                    readAllHomeAccounts(
                        { Loaded(if (it == 1) first else second, 1L, false) },
                        { published.add(it) },
                    )
                }
            }
            assertEquals(20, published.single().data.size)
        }
    }
}
