package dev.bills.app

import org.junit.Assert.*
import org.junit.Test

class RulesTest {
    private fun rejects(block: () -> Unit) {
        try {
            block()
            fail("must reject")
        } catch (_: IllegalArgumentException) {}
    }

    @Test
    fun httpsNormalization() {
        assertEquals("https://example.com:443", Rules.server(" https://EXAMPLE.com:443/ ", false))
    }

    @Test
    fun releaseRejectsCleartext() {
        listOf("http://10.0.2.2:8000", "http://localhost", "http://example.com").forEach {
            rejects { Rules.server(it, false) }
        }
    }

    @Test
    fun debugOnlyLocalHttp() {
        assertEquals("http://10.0.2.2:8000", Rules.server("http://10.0.2.2:8000/", true))
        rejects { Rules.server("http://192.168.1.2", true) }
        rejects { Rules.server("http://example.com", true) }
    }

    @Test
    fun noSecretOrParamsInAddress() {
        listOf(
                "https://user:pass@example.com",
                "https://example.com?token=a",
                "https://example.com#x",
                "https://example.com/api",
                "https://example.com//",
                "https://example.com/%2e",
                "https://example.com:0",
                "https://example.com:65536",
                "https://exa mple.com",
                "https://example.com\\evil",
            )
            .forEach { rejects { Rules.server(it, false) } }
    }

    @Test
    fun ipv6ValidHttps() {
        assertEquals("https://[::1]:8000", Rules.server("https://[::1]:8000", false))
    }

    @Test
    fun exactDecimalMoney() {
        assertEquals("123.40", Rules.amount("123.4", "CNY"))
        assertEquals("0.01", Rules.amount("0.01", "USD"))
        assertEquals("999999999999.99", Rules.amount("999999999999.99", "HKD"))
    }

    @Test
    fun negativeZeroUnknownNeverBecomePayments() {
        listOf("0", "-1", "NaN", "Infinity", "1e2", "0.001", "1000000000000", "", "1,000", "+1")
            .forEach { rejects { Rules.amount(it, "CNY") } }
    }

    @Test
    fun yenNoFraction() {
        assertEquals("100", Rules.amount("100.00", "JPY"))
        rejects { Rules.amount("100.01", "JPY") }
    }

    @Test
    fun unsupportedCurrency() {
        rejects { Rules.amount("1", "AAA") }
    }

    @Test
    fun timeUsesExplicitShanghaiOffset() {
        assertEquals("2026-10-09T09:30+08:00", Rules.instant("2026-10-09 09:30"))
    }

    @Test
    fun timeStrictDate() {
        listOf(
                "2026-02-30 09:00",
                "2026-10-09 24:00",
                "2026-10-09T09:00",
                "2026-10-09 09:00:12",
                "bad",
            )
            .forEach { rejects { Rules.instant(it) } }
    }

    @Test
    fun keysAreNewOnlyForNewLogicalActions() {
        assertNotEquals(Rules.key(), Rules.key())
        assertEquals(36, Rules.key().length)
    }

    @Test
    fun unknownIsNotZero() {
        assertEquals("未知", money(null, "CNY"))
        assertEquals("CNY 0.00", money("0.00", "CNY"))
    }

    @Test
    fun firstDefiniteValidationCanForget() {
        assertTrue(canForgetRejection(422, false))
        assertTrue(canForgetRejection(403, false))
        assertFalse(canForgetRejection(503, false))
    }

    @Test
    fun uncertainRetryNeverForgetsEarlierPossibleCommit() {
        listOf(400, 403, 404, 409, 422, 429, 500, 503).forEach {
            assertFalse(canForgetRejection(it, true))
        }
    }

    @Test
    fun pageDoesNotInventMore() {
        assertFalse(Page(listOf(1), 1, 1, 20).hasNext)
        assertTrue(Page(listOf(1), 21, 1, 20).hasNext)
        assertFalse(Page(listOf(1), 21, 2, 20).hasNext)
    }

    @Test
    fun expiryIsExclusiveAndMalformedFailsClosed() {
        val now = java.time.Instant.parse("2026-10-09T00:00:00Z")
        assertTrue(Rules.expired("2026-10-09T00:00:00Z", now))
        assertFalse(Rules.expired("2026-10-09T00:00:01Z", now))
        assertTrue(Rules.expired("bad", now))
    }

    @Test
    fun noTransactionsIsNotComplete() {
        assertTrue(detailStatus("not_provided").contains("不等于"))
        assertTrue(detailStatus("partial").contains("不完整"))
    }

    @Test
    fun missingNullOrMalformedReceiptIsNotConfirmation() {
        assertTrue(validReceiptId("0123456789abcdef0123456789abcdef"))
        listOf(null, "", "null", 42, "not-an-id").forEach { assertFalse(validReceiptId(it)) }
    }
}
