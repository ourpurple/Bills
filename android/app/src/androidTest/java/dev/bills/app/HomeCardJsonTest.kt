package dev.bills.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

// Synthetic JSON only; no pairing, server calls, or financial writes.
@RunWith(AndroidJUnit4::class)
class HomeCardJsonTest {
    private fun card(active: Boolean, tracked: Boolean, tail: String = "4853") =
        JSONObject().put("active", active).put("track_bills", tracked).put("last_four", tail)

    private fun account(vararg cards: JSONObject): Account =
        Json.account(
            JSONObject()
                .put("id", "a")
                .put("name", "测试账户")
                .put("bank_name", "中国银行")
                .put("cardholder_name", "测试持卡人")
                .put("mode", "independent")
                .put("effective_active", true)
                .put("cards", JSONArray(cards.toList()))
        )

    @Test
    fun parsesNewActiveTrackedCardWithoutNeedingStatement() {
        val parsed = account(card(true, true))
        assertEquals(listOf("4853"), parsed.tails)
        assertTrue(hasHomeBillCard(parsed))
    }

    @Test
    fun emptyInactiveAndUntrackedCardListsAreHidden() {
        for (parsed in
            listOf(
                account(),
                account(card(false, true)),
                account(card(true, false)),
                account(card(false, false)),
            )) {
            assertFalse(hasHomeBillCard(parsed))
            assertFalse(parsed.hasTrackedCard)
        }
    }

    @Test
    fun mergedAccountRequiresSameCardToBeBothActiveAndTracked() {
        assertFalse(hasHomeBillCard(account(card(true, false, "1111"), card(false, true, "2222"))))
        assertTrue(hasHomeBillCard(account(card(true, false, "1111"), card(true, true, "2222"))))
    }

    @Test
    fun olderServerWithoutTrackingFieldStillRequiresAnActiveCard() {
        val parsed = account(card(true, true).apply { remove("track_bills") })
        assertTrue(hasHomeBillCard(parsed))
        assertFalse(hasHomeBillCard(account()))
    }

    private fun statementJson() =
        JSONObject(
            """{
          "id":"s", "account":{
            "id":"a", "name":"测试账户", "bank_name":"中国银行",
            "cardholder_name":"测试持卡人", "mode":"independent", "effective_active":true,
            "cards":[{"active":true,"track_bills":true,"last_four":"4853"}]
          },
          "statement_date":"2026-09-23", "currency":"CNY", "status":"confirmed", "version":1,
          "amount":"100.00", "minimum":"10.00", "due_date":"2026-10-13",
          "paid":"0.00", "remaining":"100.00", "payment_status":"unpaid",
          "days_until_due":-17, "overdue":true, "transaction_status":"none", "source_kind":"manual"
        }"""
        )

    @Test
    fun parsesStaleStatementAndExpectedDateFromOverviewSnapshot() {
        val stale =
            statementJson().put("needs_update", true).put("expected_statement_date", "2026-10-23")
        val payload =
            JSONObject()
                .put("latest_statements", JSONArray().put(stale))
                .put("historical_unresolved", JSONArray().put(statementJson()))
                .put("pending_count", 0)
                .put("disclaimer", "测试数据")
                .put("as_of", "2026-10-30")
                .toString()
        val parsed = Json.overview(payload)
        assertTrue(parsed.latest.single().needsUpdate)
        assertEquals("2026-10-23", parsed.latest.single().expectedStatementDate)
        assertFalse(parsed.historical.single().needsUpdate)
        assertNull(parsed.historical.single().expectedStatementDate)
        // The same raw payload is parsed for network reads and encrypted cache reads.
        assertEquals(parsed, Json.overview(payload))
    }

    @Test
    fun missingAndNullFreshnessFieldsKeepLegacyStatementsReadable() {
        val legacy = Json.statement(statementJson())
        assertFalse(legacy.needsUpdate)
        assertNull(legacy.expectedStatementDate)
        val nullable =
            Json.statement(
                statementJson()
                    .put("needs_update", JSONObject.NULL)
                    .put("expected_statement_date", JSONObject.NULL)
            )
        assertEquals(legacy, nullable)
        val current =
            Json.statement(
                statementJson()
                    .put("needs_update", false)
                    .put("expected_statement_date", "2026-10-23")
            )
        assertFalse(current.needsUpdate)
        assertEquals("2026-10-23", current.expectedStatementDate)
    }

    @Test
    fun staleFlagWithoutExpectedDateDoesNotInventDate() {
        val parsed = Json.statement(statementJson().put("needs_update", true))
        assertTrue(parsed.needsUpdate)
        assertNull(parsed.expectedStatementDate)
        assertNull(billUpdateNotice(parsed))
    }
}
