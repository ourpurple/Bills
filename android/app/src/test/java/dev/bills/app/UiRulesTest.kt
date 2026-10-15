package dev.bills.app

import org.junit.Assert.*
import org.junit.Test

class UiRulesTest {
    private val account =
        Account("a", "日常账户", "招商银行", "测试持卡人", "merged", true, listOf("9512", "0377"))
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

    @Test
    fun dueLabelsReflectRealStates() {
        assertEquals(DueLabel("明", "天到期", true), dueLabel(bill))
        assertEquals("今", dueLabel(bill.copy(days = 0)).value)
        assertEquals("天后到期", dueLabel(bill.copy(days = 5)).caption)
        assertFalse(dueLabel(bill.copy(days = 5)).urgent)
        assertEquals(DueLabel("3", "天已逾期", true), dueLabel(bill.copy(days = -3, overdue = true)))
        assertEquals(
            "已标记还清",
            dueLabel(bill.copy(paymentStatus = "settled", days = -3, overdue = true)).caption,
        )
        assertEquals("已标记超额", dueLabel(bill.copy(paymentStatus = "overpaid")).caption)
        assertEquals("到期日未知", dueLabel(bill.copy(days = null, due = null)).caption)
        assertEquals("已作废", dueLabel(bill.copy(status = "void")).caption)
    }

    @Test
    fun moneyUnknownZeroAndPrivacyAreDistinct() {
        assertEquals("未知", displayAmount(null, "CNY"))
        assertEquals("0.00", displayAmount("0.00", "CNY"))
        assertEquals("12,345.67", displayAmount("12345.67", "USD"))
        assertEquals("1,000", displayAmount("1000", "JPY"))
        assertEquals("••••", displayAmount("123.45", "CNY", true))
        assertEquals("••••", displayAmount(null, "CNY", true))
    }

    @Test
    fun transactionSignUsesDirectionWithoutDoubleSigns() {
        val t = Transaction("交易", "2026-10-01", null, "3.97", "CNY", "debit", "9512")
        assertEquals("−3.97", transactionAmount(t, "CNY", false))
        assertEquals("−3.97", transactionAmount(t.copy(amount = "-3.97"), "CNY", false))
        assertEquals("+3.97", transactionAmount(t.copy(direction = "credit"), "CNY", false))
        assertEquals("未知", transactionAmount(t.copy(amount = null), "CNY", false))
        assertEquals(
            "-3.97",
            transactionAmount(t.copy(direction = "unknown", amount = "-3.97"), "CNY", false),
        )
        assertEquals("••••", transactionAmount(t, "CNY", true))
        assertEquals(
            "−100",
            transactionAmount(t.copy(amount = "100", currency = "JPY"), "CNY", false),
        )
    }

    @Test
    fun identitiesDoNotDuplicateMergedAccounts() {
        assertEquals("招行", shortBankName(account.bankName))
        assertEquals("9512、0377", accountTails(account))
        assertEquals("3 张卡", accountTails(account.copy(tails = listOf("1", "2", "3"))))
        assertEquals("无有效卡片", accountTails(account.copy(tails = emptyList())))
    }

    @Test
    fun writeActionsAreBlockedForUnsafeStates() {
        val s = UiState(busy = false, statement = bill)
        assertTrue(canWrite(s))
        assertFalse(canWrite(s.copy(cached = true)))
        assertFalse(canWrite(s.copy(busy = true)))
        assertFalse(canWrite(s.copy(statement = null)))
        assertFalse(canWrite(s.copy(statement = bill.copy(status = "draft"))))
        assertFalse(canWrite(s.copy(statement = bill.copy(account = account.copy(active = false)))))
        assertFalse(canWrite(s.copy(pending = Pending("scope", "key", "path", "{}", "pending"))))
    }

    @Test
    fun monthAndUnknownDatesAreHonest() {
        assertEquals("10月" to "2026", monthTitle(bill.date))
        assertEquals("10-15", shortDate(bill.due))
        assertEquals("未知", shortDate(null))
    }

    @Test
    fun cachedNestedReadCannotBecomeOnlineInRetainedHistory() {
        val s =
            UiState(
                busy = false,
                screen = "detail",
                selectedAccount = account,
                history = Page(listOf(bill), 1, 1, 20),
                historyAccountId = account.id,
                cached = true,
                cacheAt = 100L,
                historyCached = false,
                historyAt = 200L,
            )
        val remembered = s.rememberHistoryRead()
        assertTrue(remembered.historyCached)
        assertEquals(100L, remembered.historyAt)
        val home =
            remembered.copy(screen = "home", cached = false, cacheAt = 300L).rememberHistoryRead()
        assertTrue(home.historyCached)
        assertEquals(100L, home.historyAt)
    }

    @Test
    fun unrelatedDetailDoesNotChangeAnotherAccountsHistoryFreshness() {
        val s =
            UiState(
                screen = "detail",
                selectedAccount = account.copy(id = "another"),
                history = Page(listOf(bill), 1, 1, 20),
                historyAccountId = account.id,
                cached = true,
                cacheAt = 100L,
                historyCached = false,
                historyAt = 200L,
            )
        assertEquals(s, s.rememberHistoryRead())
        val refreshed =
            s.copy(screen = "details", cached = false, cacheAt = 300L).rememberHistoryRead()
        assertFalse(refreshed.historyCached)
        assertEquals(300L, refreshed.historyAt)
    }

    @Test
    fun accountEntryClearsPreviousDetailsAndStartsCollapsed() {
        val previous =
            UiState(
                screen = "home",
                busy = false,
                selectedAccount = account.copy(id = "old-account"),
                history = Page(listOf(bill), 1, 1, 20),
                historyAccountId = "old-account",
                statement = bill,
                expandedStatementId = bill.id,
                detailSection = "plans",
                transactions = Page(emptyList(), 0, 1, 20),
                transactionsIncomplete = true,
                repayments = Page(emptyList(), 0, 1, 20),
                plans = Page(emptyList(), 0, 1, 20),
                cached = false,
                cacheAt = 100L,
            )
        val opened = previous.openAccountHistory(account)
        assertEquals("details", opened.screen)
        assertEquals(account, opened.selectedAccount)
        assertEquals("transactions", opened.detailSection)
        assertNull(opened.history)
        assertNull(opened.historyAccountId)
        assertNull(opened.expandedStatementId)
        assertNull(opened.statement)
        assertNull(opened.transactions)
        assertFalse(opened.transactionsIncomplete)
        assertNull(opened.repayments)
        assertNull(opened.plans)
        assertNull(opened.cacheAt)
        assertTrue(opened.cached)
        assertFalse(canWrite(opened))
    }

    @Test
    fun reenteringSameAccountAlsoResetsExpansionButKeepsPrivacyAndPendingWrite() {
        val pending = Pending("scope", "key", "path", "{}", "pending")
        val opened =
            UiState(
                    busy = false,
                    selectedAccount = account,
                    statement = bill,
                    expandedStatementId = bill.id,
                    pending = pending,
                    amountsHidden = true,
                    defaultAccountId = "default-account",
                )
                .openAccountHistory(account)
        assertNull(opened.expandedStatementId)
        assertNull(opened.statement)
        assertEquals(pending, opened.pending)
        assertTrue(opened.amountsHidden)
        assertEquals("default-account", opened.defaultAccountId)
        assertFalse(canWrite(opened))
    }

    @Test
    fun monthPagesNeverAutoExpandOrLoseCacheProvenance() {
        val months = listOf(bill, bill.copy(id = "older", date = "2026-09-09"))
        for (cached in listOf(false, true)) {
            for (number in listOf(1, 2)) {
                val page = Page(months, 4, number, 2)
                val state =
                    UiState(
                            busy = false,
                            selectedAccount = account,
                            statement = bill,
                            expandedStatementId = bill.id,
                            transactions = Page(emptyList(), 0, 1, 20),
                        )
                        .withHistoryPage(account.id, Loaded(page, 123L, cached))
                assertEquals(page, state.history)
                assertEquals(account.id, state.historyAccountId)
                assertNull(state.expandedStatementId)
                assertNull(state.statement)
                assertNull(state.transactions)
                assertEquals(cached, state.cached)
                assertEquals(cached, state.historyCached)
                assertEquals(123L, state.cacheAt)
                assertEquals(123L, state.historyAt)
                assertFalse(canWrite(state))
            }
        }
    }
}
