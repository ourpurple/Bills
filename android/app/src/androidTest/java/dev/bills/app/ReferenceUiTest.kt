package dev.bills.app

import android.graphics.Bitmap
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

// Synthetic fixtures only: these tests never pair, call an API, or change server records.
@RunWith(AndroidJUnit4::class)
class ReferenceUiTest {
    @get:Rule val compose = createComposeRule()
    private val account = Account("a", "日常账户", "招商银行", "测试持卡人", "merged", true, listOf("9512"))
    private val bill =
        Statement(
            "s",
            account,
            "2026-10-09",
            "CNY",
            "confirmed",
            1,
            "2680.00",
            "100.00",
            "2026-10-15",
            "0.00",
            "2680.00",
            "unpaid",
            1,
            false,
            "complete",
            "2026-09-10",
            "2026-10-09",
            "manual",
        )
    private val tx =
        Transaction("消费郑州地铁集团有限公司运营分公司", "2026-10-08", null, "3.97", "CNY", "debit", "9512")
    private val banks = listOf("招商银行", "农业银行", "工商银行", "招商银行", "华夏银行")
    private val bills =
        banks.mapIndexed { i, bank ->
            bill.copy(
                id = "s$i",
                account =
                    account.copy(
                        id = "a$i",
                        bankName = bank,
                        tails = listOf(listOf("9512", "4029", "3484", "0377", "3946")[i]),
                    ),
                days = if (i < 3) 1 else i,
                due = if (i < 3) "2026-10-15" else "2026-10-${14+i}",
                amount = if (i == 0) "2680.00" else null,
                remaining = if (i == 0) "2680.00" else null,
            )
        }
    private val base =
        UiState(
            connection = Connection("https://example.invalid", "界面测试", "2026-12-01T00:00:00+08:00"),
            busy = false,
            cacheAt = 1791966600000L,
            home =
                Overview(
                    listOf(Total("CNY", "2680.00")),
                    bills,
                    emptyList(),
                    emptyList(),
                    0,
                    "仅为账单档案与个人还款标记，不代表银行实时余额。",
                    "2026-10-14",
                ),
            accounts = Page(bills.map { it.account }, 5, 1, 20),
            selectedAccount = account,
            history = Page(listOf(bill), 1, 1, 20),
            historyAccountId = account.id,
            statement = bill,
            expandedStatementId = bill.id,
            transactions =
                Page(
                    listOf(
                        tx,
                        tx,
                        tx.copy(description = "消费财付通-全日鲜生活便利店", amount = "16.01"),
                        tx.copy(amount = "3.95"),
                        tx.copy(amount = "2.00"),
                    ),
                    5,
                    1,
                    20,
                    1,
                    "complete",
                ),
            repayments = Page(emptyList(), 0, 1, 20),
            plans = Page(emptyList(), 0, 1, 20),
        )
    private val state = mutableStateOf(base)

    private fun show(initial: UiState = base, narrow: Boolean = false, largeFont: Boolean = false) {
        state.value = initial
        compose.setContent {
            val density = LocalDensity.current
            CompositionLocalProvider(
                LocalDensity provides
                    Density(density.density, if (largeFont) 1.6f else density.fontScale)
            ) {
                Box(if (narrow) Modifier.width(320.dp) else Modifier) {
                    BillsSurface(
                        state.value,
                        UiActions(
                            tab = { state.value = state.value.copy(screen = it) },
                            back = {
                                state.value = state.value.copy(screen = state.value.detailReturn)
                            },
                            hideAmounts = { state.value = state.value.copy(amountsHidden = it) },
                            section = { state.value = state.value.copy(detailSection = it) },
                            account = { selected ->
                                val months =
                                    (listOf(bill) + bills)
                                        .filter { it.account.id == selected.id }
                                        .flatMap {
                                            listOf(
                                                it,
                                                it.copy(
                                                    id = "${it.id}-previous",
                                                    date = "2026-09-09",
                                                ),
                                            )
                                        }
                                state.value =
                                    state.value
                                        .openAccountHistory(selected)
                                        .withHistoryPage(
                                            selected.id,
                                            Loaded(
                                                Page(months, months.size, 1, 20),
                                                123L,
                                                initial.cached,
                                            ),
                                        )
                            },
                            detail = {
                                state.value =
                                    state.value.copy(
                                        screen = "detail",
                                        statement = it,
                                        detailReturn = "home",
                                    )
                            },
                            expand = { row ->
                                state.value =
                                    if (state.value.expandedStatementId == row.id)
                                        state.value.copy(expandedStatementId = null)
                                    else
                                        state.value.copy(
                                            statement = row,
                                            expandedStatementId = row.id,
                                            transactions =
                                                base.transactions!!.copy(version = row.version),
                                        )
                            },
                        ),
                    )
                }
            }
        }
    }

    private fun assertDuplicateRows(prefix: String, screen: String) {
        listOf(0, 1).forEach { index ->
            val tag = "$prefix-tx-1-$index"
            compose.onNodeWithTag(screen).performScrollToNode(hasTestTag(tag))
            compose.onNodeWithTag(tag).assertIsDisplayed()
            compose
                .onNode(hasText(tx.description) and hasAnyAncestor(hasTestTag(tag)))
                .assertExists()
        }
    }

    private fun snapshot(name: String) {
        compose.waitForIdle()
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val output = File(context.getExternalFilesDir(null), "ui-review").apply { mkdirs() }
        File(output, "$name.png").outputStream().use {
            compose
                .onRoot()
                .captureToImage()
                .asAndroidBitmap()
                .compress(Bitmap.CompressFormat.PNG, 100, it)
        }
    }

    @Test
    fun threeTabsAndBankListMatchReferenceStructure() {
        show()
        listOf("home", "details", "settings").forEach {
            compose.onNodeWithTag("nav-$it").assertExists()
        }
        compose.onNodeWithText("信用卡").assertIsDisplayed()
        compose.onNodeWithText("全部待还").assertIsDisplayed()
        compose.onNodeWithText("更新账单").assertDoesNotExist()
        compose.onNodeWithText("立即还款").assertDoesNotExist()
        snapshot("01-bills")
        compose.onNodeWithTag("nav-details").performClick()
        compose.onNodeWithText("账单明细").assertIsDisplayed()
        compose.onNodeWithTag("month-s").assertIsDisplayed()
        snapshot("02-details")
        compose.onNodeWithTag("nav-settings").performClick()
        compose.onNodeWithText("连接与设备").assertIsDisplayed()
        snapshot("04-settings")
        compose.onNodeWithTag("nav-home").performClick()
        compose.onNodeWithText("信用卡").assertIsDisplayed()
    }

    @Test
    fun homeSummaryRemovesMarkedBlocksAndPlacesTotalOnRight() {
        show()
        compose.onNode(hasText("笔待核对", substring = true)).assertDoesNotExist()
        compose.onNodeWithText("全部账户 · 最新已确认账单 · 按币种汇总").assertDoesNotExist()
        compose.onNodeWithText("本期账单").assertDoesNotExist()
        compose.onNodeWithTag("overview-total-CNY").assertTextEquals("CNY  2,680.00")
        val label = compose.onNodeWithTag("overview-label").fetchSemanticsNode().boundsInRoot
        val total = compose.onNodeWithTag("overview-total-CNY").fetchSemanticsNode().boundsInRoot
        val summary = compose.onNodeWithTag("overview-summary").fetchSemanticsNode().boundsInRoot
        assertTrue(label.right < total.left)
        assertEquals(label.center.y, total.center.y, 1f)
        assertEquals(summary.right, total.right, 1f)
    }

    @Test
    fun rightAlignedSummaryKeepsCurrenciesSeparateAndRespectsPrivacyOnNarrowLargeFont() {
        show(
            base.copy(
                home =
                    base.home!!.copy(
                        totals = listOf(Total("CNY", "2680.00"), Total("USD", "123.45"))
                    )
            ),
            narrow = true,
            largeFont = true,
        )
        val label = compose.onNodeWithTag("overview-label").fetchSemanticsNode().boundsInRoot
        val summary = compose.onNodeWithTag("overview-summary").fetchSemanticsNode().boundsInRoot
        listOf("CNY" to "2,680.00", "USD" to "123.45").forEach { (currency, amount) ->
            val node = compose.onNodeWithTag("overview-total-$currency")
            node.assertIsDisplayed().assertTextEquals("$currency  $amount")
            val bounds = node.fetchSemanticsNode().boundsInRoot
            assertTrue(bounds.left > label.right)
            assertEquals(summary.right, bounds.right, 1f)
            assertTrue(bounds.top >= summary.top && bounds.bottom <= summary.bottom)
        }
        compose.onNodeWithTag("toggle-amounts").performClick()
        listOf("CNY", "USD").forEach { currency ->
            compose.onNodeWithTag("overview-total-$currency").assertTextEquals("$currency  ••••")
        }
        compose.onNode(hasText("2,680.00", substring = true)).assertDoesNotExist()
        compose.onNode(hasText("123.45", substring = true)).assertDoesNotExist()
        compose.onNodeWithTag("toggle-amounts").performClick()
        compose.onNodeWithTag("overview-total-CNY").assertTextEquals("CNY  2,680.00")
        compose.onNodeWithTag("overview-total-USD").assertTextEquals("USD  123.45")
    }

    @Test
    fun summaryWithoutConfirmedTotalsDoesNotDisplayZero() {
        show(base.copy(home = base.home!!.copy(totals = emptyList())))
        compose.onNodeWithText("金额未知 / 尚无已确认账单").assertIsDisplayed()
        compose.onNodeWithTag("overview-total-CNY").assertDoesNotExist()
        compose.onNodeWithText("0.00").assertDoesNotExist()
    }

    @Test
    fun billModesRemainAvailableInsideFilterAndResetRestoresCurrent() {
        show(
            base.copy(
                home =
                    base.home!!.copy(
                        latest = listOf(bills[0], bills[1].copy(days = 12)),
                        historical = listOf(bill.copy(id = "old")),
                    ),
                accounts = null,
            )
        )
        compose.onNodeWithTag("bill-s0").assertIsDisplayed()
        compose.onNodeWithTag("bill-s1").assertIsDisplayed()
        compose.onNodeWithText("筛选").performClick()
        compose.onNodeWithText("账单范围").assertIsDisplayed()
        compose.onNodeWithText("本期账单").performClick()
        compose.onNodeWithText("近期到期（7天内）").performClick()
        compose.onNodeWithText("完成").performClick()
        compose.onNodeWithTag("bill-s0").assertIsDisplayed()
        compose.onNodeWithTag("bill-s1").assertDoesNotExist()
        compose.onNodeWithText("筛选").performClick()
        compose.onNodeWithText("近期到期（7天内）").performClick()
        compose.onNodeWithText("历史未结清").performClick()
        compose.onNodeWithText("完成").performClick()
        compose.onNodeWithTag("bill-old").assertIsDisplayed()
        compose.onNodeWithTag("bill-s0").assertDoesNotExist()
        compose.onNodeWithText("筛选").performClick()
        compose.onNodeWithText("重置").performClick()
        compose.onNodeWithText("本期账单").assertIsDisplayed()
        compose.onNodeWithText("完成").performClick()
        compose.onNodeWithTag("bill-s0").assertIsDisplayed()
        compose.onNodeWithTag("bill-s1").assertIsDisplayed()
        compose.onNodeWithTag("bill-old").assertDoesNotExist()
        compose.onNodeWithText("本期账单").assertDoesNotExist()
    }

    private fun openHomeBill(id: String) {
        compose.onNodeWithTag("screen-home").performScrollToNode(hasTestTag("bill-$id"))
        compose.onNodeWithTag("bill-$id").performClick()
        compose.onNodeWithTag("screen-details").assertExists()
    }

    @Test
    fun homeBillOpensAccountsMonthsCollapsedAndExpandsEachOnDemand() {
        show()
        openHomeBill("s0")
        compose.onNodeWithTag("month-s0").assertIsDisplayed()
        compose.onNodeWithTag("month-s0-previous").assertIsDisplayed()
        compose.onNodeWithTag("month-s").assertDoesNotExist()
        compose.onNodeWithTag("mark-payment").assertDoesNotExist()
        compose.onNodeWithText(tx.description).assertDoesNotExist()
        compose.runOnIdle {
            assertEquals("a0", state.value.selectedAccount?.id)
            assertEquals(2, state.value.history?.items?.size)
            assertNull(state.value.expandedStatementId)
            assertNull(state.value.statement)
            assertNull(state.value.transactions)
        }
        compose.onNodeWithTag("month-s0").performClick()
        assertDuplicateRows("s0", "screen-details")
        compose.onNodeWithTag("screen-details").performScrollToNode(hasTestTag("month-s0"))
        compose.onNodeWithTag("month-s0").performClick()
        compose.onNodeWithText(tx.description).assertDoesNotExist()
        compose.onNodeWithTag("month-s0-previous").performClick()
        assertDuplicateRows("s0-previous", "screen-details")
        compose.onNodeWithTag("s0-tx-1-0").assertDoesNotExist()
        compose.runOnIdle { assertEquals("s0-previous", state.value.statement?.id) }
    }

    @Test
    fun homeReentryAndAccountSwitchNeverRetainExpandedMonths() {
        show()
        openHomeBill("s0")
        compose.onNodeWithTag("month-s0").performClick()
        compose.onNodeWithTag("nav-home").performClick()
        openHomeBill("s0")
        compose.onNodeWithText(tx.description).assertDoesNotExist()
        compose.runOnIdle { assertNull(state.value.expandedStatementId) }
        compose.onNodeWithTag("month-s0").performClick()
        compose.onNodeWithTag("nav-home").performClick()
        openHomeBill("s2")
        compose.onNodeWithTag("month-s2").assertIsDisplayed()
        compose.onNodeWithTag("month-s2-previous").assertIsDisplayed()
        compose.onNodeWithTag("month-s0").assertDoesNotExist()
        compose.onNodeWithText(tx.description).assertDoesNotExist()
        compose.runOnIdle {
            assertEquals("a2", state.value.selectedAccount?.id)
            assertNull(state.value.expandedStatementId)
            assertNull(state.value.statement)
        }
    }

    @Test
    fun cachedHomeMonthEntryStaysReadOnlyWhenOpenedForPayment() {
        show(base.copy(cached = true))
        openHomeBill("s0")
        compose.onNodeWithText("缓存 · 非最新 · 只读").assertIsDisplayed()
        compose.onNodeWithText(tx.description).assertDoesNotExist()
        compose.onNodeWithTag("month-s0").performClick()
        compose.onNodeWithTag("details-more").performClick()
        compose.onNodeWithTag("open-statement").performClick()
        compose.onNodeWithTag("mark-payment").assertIsNotEnabled()
    }

    @Test
    fun monthListOmitsPagingControlsAndOldExplanationEvenBeyondTwentyMonths() {
        val months = List(25) { bill.copy(id = "month-$it") }
        show(
            base.copy(
                screen = "details",
                history = Page(months, 25, 1, 25),
                expandedStatementId = null,
                statement = null,
                transactions = null,
            )
        )
        compose.onNodeWithTag("screen-details").performScrollToNode(hasTestTag("month-month-24"))
        compose.onNodeWithTag("month-month-24").assertIsDisplayed().performClick()
        assertDuplicateRows("month-24", "screen-details")
        compose.onNodeWithTag("screen-details").performScrollToNode(hasTestTag("month-month-24"))
        compose.onNodeWithTag("month-month-24").performClick()
        compose.onNodeWithText(tx.description).assertDoesNotExist()
        compose.onNodeWithText("上一页").assertDoesNotExist()
        compose.onNodeWithText("下一页").assertDoesNotExist()
        compose.onNode(hasText("第 1 页", substring = true)).assertDoesNotExist()
        compose.onNode(hasText("仅展示当前分页账期", substring = true)).assertDoesNotExist()
        compose.onNodeWithText("月份账单尚未完整读取，请下拉重试").assertDoesNotExist()
    }

    @Test
    fun interruptedMonthListShowsRetryHintInsteadOfSilentTruncation() {
        show(
            base.copy(
                screen = "details",
                history = Page(listOf(bill), 25, 1, 25),
                expandedStatementId = null,
                cached = true,
            )
        )
        compose.onNodeWithTag("month-s").assertIsDisplayed()
        compose.onNodeWithTag("screen-details").performScrollToNode(hasText("月份账单尚未完整读取，请下拉重试"))
        compose.onNodeWithText("月份账单尚未完整读取，请下拉重试").assertIsDisplayed()
        compose.onNodeWithText("上一页").assertDoesNotExist()
        compose.onNodeWithText("下一页").assertDoesNotExist()
    }

    @Test
    fun homeOmitsWarningBlockAndUpdateButtonsIncludingMissingStatements() {
        val warning = "本期金额未知，请核对账单是否已收到"
        val missing = account.copy(id = "missing", bankName = "中信银行")
        show(
            base.copy(
                home = base.home!!.copy(warnings = listOf(Warning(missing.id, "中信", warning))),
                accounts = Page(bills.map { it.account } + missing, 6, 1, 20),
            )
        )
        compose.onNode(hasText(warning, substring = true)).assertDoesNotExist()
        compose.onNodeWithText("更新账单").assertDoesNotExist()
        compose.onNodeWithText("全部待还").assertIsDisplayed()
        compose.onNodeWithTag("screen-home").performScrollToNode(hasText("尚无本期已确认账单 · 到期日未知"))
        compose.onNodeWithText("尚无本期已确认账单 · 到期日未知").assertIsDisplayed()
        compose.onNodeWithText("更新账单").assertDoesNotExist()
        compose.onNodeWithTag("nav-home").assertIsDisplayed()
    }

    @Test
    fun simplifiedDetailsOmitRedBoxRowsAndKeepActionsInMoreMenu() {
        show(base.copy(screen = "details"))
        compose.onNodeWithTag("sync-line").assertDoesNotExist()
        compose.onNodeWithText("刷新").assertDoesNotExist()
        compose.onNodeWithText("查看详情 / 标记还款").assertDoesNotExist()
        compose.onNode(hasText("待还 CNY", substring = true)).assertDoesNotExist()
        compose.onNodeWithText("已提取详单（仍以银行为准）").assertDoesNotExist()
        compose.onNodeWithTag("s-tx-1-0").assertIsDisplayed()
        compose.onNodeWithTag("details-more").performClick()
        compose.onNodeWithText("选择还款账户").assertExists()
        compose.onNodeWithTag("open-statement").performClick()
        compose.onNodeWithTag("mark-payment").assertIsDisplayed().assertIsEnabled()
        compose.onNodeWithTag("sync-line").assertDoesNotExist()
        compose.onNode(hasText("记录待还", substring = true)).assertDoesNotExist()
    }

    @Test
    fun simplifiedDetailsStillWarnWhenCachedOrPageIsIncomplete() {
        show(base.copy(screen = "details", cached = true))
        compose.onNodeWithTag("sync-line").assertDoesNotExist()
        compose.onNodeWithText("缓存 · 非最新 · 只读").assertIsDisplayed()
        compose.runOnIdle {
            state.value =
                state.value.copy(
                    cached = false,
                    transactions = base.transactions!!.copy(detailStatus = "partial"),
                )
        }
        compose.onNodeWithText("缓存 · 非最新 · 只读").assertDoesNotExist()
        compose.onNode(hasText("详单不完整", substring = true)).assertIsDisplayed()
    }

    @Test
    fun transactionListShowsOnlyCountWithoutPagingControls() {
        show(base.copy(screen = "detail"))
        compose.onNodeWithTag("screen-detail").performScrollToNode(hasTestTag("transaction-count"))
        compose.onNodeWithText("共 5 条").assertIsDisplayed()
        compose.onNodeWithText("上一页").assertDoesNotExist()
        compose.onNodeWithText("下一页").assertDoesNotExist()
        compose.onNode(hasText("第 1 页", substring = true)).assertDoesNotExist()
        compose.runOnIdle { state.value = state.value.copy(screen = "details") }
        compose.onNodeWithTag("screen-details").performScrollToNode(hasTestTag("transaction-count"))
        compose.onNodeWithText("共 5 条").assertIsDisplayed()
        compose.onNodeWithTag("s-tx-1-4").assertExists()
    }

    @Test
    fun interruptedContinuousReadDoesNotPretendEveryTransactionWasDisplayed() {
        show(
            base.copy(
                screen = "detail",
                transactions = base.transactions!!.copy(total = 25),
                transactionsIncomplete = true,
            )
        )
        compose.onNodeWithTag("screen-detail").performScrollToNode(hasTestTag("transaction-count"))
        compose.onNodeWithText("共 25 条").assertIsDisplayed()
        compose.onNodeWithText("仅显示 5 条，未完整读取，请下拉重试").assertIsDisplayed()
        compose.onNodeWithText("下一页").assertDoesNotExist()
    }

    @Test
    fun nestedDetailKeepsDuplicatesAndShowsHonestPaymentAction() {
        show(base.copy(screen = "detail"))
        compose.onNodeWithTag("nav-home").assertDoesNotExist()
        compose.onNodeWithTag("back").assertExists()
        assertDuplicateRows("detail", "screen-detail")
        compose.onNodeWithTag("mark-payment").assertIsEnabled().assertIsDisplayed()
        compose.onNodeWithText("立即还款").assertDoesNotExist()
        snapshot("03-statement")
        compose.onNodeWithTag("mark-payment").performClick()
        compose.onNode(hasText("不证明银行到账", substring = true)).assertExists()
        compose.onNodeWithText("取消").performClick()
        compose.onNodeWithText("计划还款").performClick()
        compose.onNode(hasText("通知功能尚未实现", substring = true)).assertExists()
    }

    @Test
    fun cachedAndUncertainSubmissionsBlockNewWrites() {
        show(base.copy(screen = "detail", cached = true))
        compose.onNodeWithTag("mark-payment").assertIsNotEnabled()
        compose.onNode(hasText("缓存 · 非最新", substring = true)).assertExists()
        compose.runOnIdle {
            state.value =
                base.copy(
                    screen = "detail",
                    pending = Pending("scope", "key", "/path", "{}", "记录还款", uncertain = true),
                )
        }
        compose.onNodeWithTag("mark-payment").assertIsNotEnabled()
        compose.onNodeWithText("核对重试（不新建）").assertExists()
    }

    @Test
    fun privacyHidesSummaryTransactionsAndPaymentRecords() {
        show(base.copy(screen = "detail", amountsHidden = true))
        compose.onNodeWithText("2,680.00").assertDoesNotExist()
        compose.onNodeWithText("−3.97").assertDoesNotExist()
        compose.onAllNodesWithText("••••").onFirst().assertExists()
        compose.runOnIdle {
            state.value =
                state.value.copy(
                    detailSection = "repayments",
                    repayments =
                        Page(
                            listOf(
                                PaymentRecord(
                                    "r",
                                    "25.00",
                                    "CNY",
                                    "2026-10-14T10:00:00+08:00",
                                    "",
                                    null,
                                    null,
                                )
                            ),
                            1,
                            1,
                            20,
                        ),
                )
        }
        compose.onNodeWithText("CNY 25.00").assertDoesNotExist()
        compose.onNodeWithText("CNY ••••").assertExists()
    }

    @Test
    fun missingAndPartialDetailsNeverImplyNoSpending() {
        show(
            base.copy(
                screen = "detail",
                statement = bill.copy(transactionStatus = "partial"),
                transactions = Page(emptyList(), 0, 1, 20, 1, "partial"),
            )
        )
        compose.onNode(hasText("详单不完整", substring = true)).assertExists()
        compose.onNodeWithText("尚无可展示详单").assertExists()
        compose.runOnIdle {
            state.value =
                state.value.copy(
                    statement = bill.copy(transactionStatus = "not_provided"),
                    transactions = Page(emptyList(), 0, 1, 20, 1, "not_provided"),
                )
        }
        compose.onNode(hasText("不等于无交易", substring = true)).assertExists()
    }

    @Test
    fun collapseRetainsAccountAndDoesNotExposeUnselectedTransactions() {
        show(base.copy(screen = "details"))
        compose.onNodeWithTag("month-s").performClick()
        compose.onNodeWithText(tx.description).assertDoesNotExist()
        compose.onNodeWithTag("month-s").performClick()
        assertDuplicateRows("s", "screen-details")
    }

    private fun assertNestedMonthToggles() {
        repeat(3) {
            compose.onNodeWithTag("screen-detail").performScrollToNode(hasTestTag("month-s"))
            // Tap the triangle area itself, not just the month's text or semantics action.
            compose.onNodeWithTag("month-s").assertIsEnabled().performTouchInput {
                click(androidx.compose.ui.geometry.Offset(24.dp.toPx(), center.y))
            }
            compose.onNodeWithText(tx.description).assertDoesNotExist()
            compose.onNodeWithTag("transaction-count").assertDoesNotExist()
            compose.onNodeWithTag("mark-payment").assertIsDisplayed()
            compose.runOnIdle {
                assertNull(state.value.expandedStatementId)
                assertEquals(account.id, state.value.selectedAccount?.id)
                assertEquals(base.transactions, state.value.transactions)
            }
            compose.onNodeWithTag("month-s").performClick()
            assertDuplicateRows("detail", "screen-detail")
            compose.runOnIdle { assertEquals(bill.id, state.value.expandedStatementId) }
        }
    }

    @Test
    fun nestedDetailTriangleCollapsesAndReopensRepeatedly() {
        show(base.copy(screen = "detail"))
        assertNestedMonthToggles()
        compose.onNodeWithTag("mark-payment").assertIsEnabled()
    }

    @Test
    fun cachedNestedDetailCanToggleWithoutEnablingPayment() {
        show(base.copy(screen = "detail", cached = true))
        assertNestedMonthToggles()
        compose.onNodeWithTag("mark-payment").assertIsNotEnabled()
        compose
            .onNodeWithTag("screen-detail")
            .performScrollToNode(hasText("当前为只读", substring = true))
        compose.onNode(hasText("当前为只读", substring = true)).assertIsDisplayed()
        compose.runOnIdle { assertTrue(state.value.cached) }
    }

    @Test
    fun nestedDetailMonthBecomesInteractiveAfterLoading() {
        show(base.copy(screen = "detail", busy = true))
        compose.onNodeWithTag("month-s").assertIsNotEnabled().performTouchInput { click() }
        compose.runOnIdle {
            assertEquals(bill.id, state.value.expandedStatementId)
            state.value = state.value.copy(busy = false)
        }
        compose.onNodeWithTag("month-s").assertIsEnabled().performClick()
        compose.onNodeWithText(tx.description).assertDoesNotExist()
        compose.runOnIdle { assertNull(state.value.expandedStatementId) }
    }

    @Test
    fun compactNavigationAndDetailControlsKeepMinimumTouchHeight() {
        show()
        listOf("home", "details", "settings").forEach {
            compose.onNodeWithTag("nav-$it").assertIsDisplayed().assertHeightIsAtLeast(48.dp)
        }
        compose.onNodeWithTag("nav-details").performClick()
        compose.onNodeWithTag("section-transactions").assertHeightIsAtLeast(48.dp)
        compose.onNodeWithTag("section-repayments").assertHeightIsAtLeast(48.dp)
        compose.onNodeWithTag("month-s").assertHeightIsAtLeast(48.dp)
        compose.runOnIdle { state.value = state.value.copy(screen = "detail") }
        compose.onNodeWithTag("mark-payment").assertIsDisplayed().assertHeightIsAtLeast(48.dp)
    }

    @Test
    fun largeFontSmallWidthAndLongNamesKeepControlsAccessible() {
        val longBill =
            bill.copy(
                account =
                    account.copy(
                        holderName = "用于界面测试的较长持卡人名称",
                        tails = listOf("9512", "3484", "0377"),
                    )
            )
        show(
            base.copy(home = base.home!!.copy(latest = listOf(longBill))),
            narrow = true,
            largeFont = true,
        )
        compose.onNodeWithTag("screen-home").performScrollToNode(hasTestTag("bill-s"))
        compose.onNodeWithTag("bill-s").assertIsDisplayed()
        compose.onNodeWithText("更新账单").assertDoesNotExist()
        compose.onNodeWithTag("nav-settings").assertIsDisplayed()
        snapshot("05-large-font")
        compose.runOnIdle {
            state.value = state.value.copy(screen = "detail", statement = longBill)
        }
        compose.onNodeWithTag("mark-payment").assertIsDisplayed().assertIsEnabled()
        compose.onNodeWithText("计划还款").assertIsDisplayed()
    }
}
