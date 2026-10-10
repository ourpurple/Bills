package dev.bills.app

import android.content.Context
import android.content.ContextWrapper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import java.io.IOException
import java.net.URL
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

private const val SERVER = "http://10.0.2.2:8014"

internal fun fixture(): JSONObject =
    URL("$SERVER/testing/fixture")
        .openConnection()
        .apply {
            connectTimeout = 10000
            readTimeout = 15000
        }
        .getInputStream()
        .use { JSONObject(it.bufferedReader().readText()) }

@RunWith(AndroidJUnit4::class)
class StorageFlowTest {
    private fun context(name: String): Context {
        val target = InstrumentationRegistry.getInstrumentation().targetContext
        return object : ContextWrapper(target) {
            override fun getNoBackupFilesDir() =
                File(target.noBackupFilesDir, name).apply { mkdirs() }
        }
    }

    @Test
    fun encryptionRoomAndScope() = runBlocking {
        val ctx = context("vault-test")
        val vault = Vault(ctx, "bills-instrumented-storage")
        val db = CacheDb.open(ctx)
        try {
            vault.destroy()
            db.cache().clear()
            vault.write("session", "synthetic-token-private")
            assertEquals("synthetic-token-private", vault.read("session"))
            val encrypted = File(ctx.noBackupFilesDir, "private/session").readBytes()
            assertFalse(encrypted.toString(Charsets.UTF_8).contains("synthetic-token-private"))
            val payload = vault.encrypt("CNY 1000.00 测试持卡人")
            assertFalse(payload.toString(Charsets.UTF_8).contains("1000.00"))
            db.cache().put(CacheRow("device-a/overview", payload, 100))
            assertNull(db.cache().get("device-b/overview"))
            assertEquals(
                "CNY 1000.00 测试持卡人",
                vault.decrypt(db.cache().get("device-a/overview")!!.payload),
            )
            for (i in 1..205) db.cache().put(CacheRow("$i", payload, i.toLong()))
            db.cache().prune()
            assertNull(db.cache().get("1"))
            assertNotNull(db.cache().get("205"))
            payload[15] = (payload[15].toInt() xor 1).toByte()
            try {
                vault.decrypt(payload)
                fail("tampered data accepted")
            } catch (_: Exception) {}
        } finally {
            vault.destroy()
            db.cache().clear()
            db.close()
        }
    }

    @Test
    fun realBackendOfflineUncertainRetryProcessRestoreAndRevocation() = runBlocking {
        val ctx = context("repository-test")
        val vault = Vault(ctx, "bills-instrumented-repository")
        val db = CacheDb.open(ctx)
        val switches =
            object : Api() {
                var offline = false
                var dropReceipt = false
                var retry503 = false
                var corruptReceipt = false

                override suspend fun call(
                    server: String,
                    path: String,
                    token: String?,
                    body: String?,
                    key: String?,
                ): String {
                    if (offline) throw IOException("simulated connection loss")
                    if (retry503) {
                        retry503 = false
                        throw ApiError(503, "UNAVAILABLE", "test")
                    }
                    val response = super.call(server, path, token, body, key)
                    if (dropReceipt && body != null && path.endsWith("/repayments")) {
                        dropReceipt = false
                        throw IOException("receipt lost after commit")
                    }
                    if (corruptReceipt && body != null && path.endsWith("/repayments")) {
                        corruptReceipt = false
                        return "{\"id\":null}"
                    }
                    return response
                }
            }
        try {
            val f = fixture()
            val sid = f.getString("statement_id")
            var repo = Repository(vault, db, switches)
            repo.restore()
            repo.clear()
            repo.pair(SERVER, f.getString("code"), "仪器化测试")
            val scope = repo.session!!.scope
            val home = repo.read("/api/v1/app/overview", Json::overview)
            assertFalse(home.cached)
            switches.offline = true
            assertTrue(repo.read("/api/v1/app/overview", Json::overview).cached)
            assertNull(repo.pending) // No automatic write or payment queue.
            switches.offline = false
            val summary =
                repo.read("/api/v1/app/statements/$sid/summary") { Json.statement(JSONObject(it)) }
            val version = summary.data.version
            val first =
                repo.read(
                    "/api/v1/app/statements/$sid/transactions?version=$version&page=1&page_size=20"
                ) {
                    Json.page(it, Json::transaction)
                }
            val next =
                repo.read(
                    "/api/v1/app/statements/$sid/transactions?version=$version&page=2&page_size=20"
                ) {
                    Json.page(it, Json::transaction)
                }
            assertEquals(20, first.data.items.size)
            assertEquals(5, next.data.items.size)
            assertEquals(25, first.data.total)
            assertEquals(first.data.items[0], first.data.items[1])
            val before = summary.data.paid.toBigDecimal()
            val note = "安卓测试-${UUID.randomUUID()}"
            val payment =
                JSONObject()
                    .put("amount", "50.00")
                    .put("currency", "CNY")
                    .put("occurred_at", "2026-09-10T10:00:00+08:00")
                    .put("note", note)
                    .put("confirm_exception", false)
                    .toString()
            repo.stage("/api/v1/app/statements/$sid/repayments", payment, "记录还款")
            val key = repo.pending!!.key
            switches.dropReceipt = true
            try {
                repo.submit()
                fail("missing receipt reported as success")
            } catch (_: IOException) {}
            assertEquals(key, repo.pending!!.key)
            assertTrue(repo.pending!!.uncertain)
            // Process restart restores exactly the same content and logical submission key.
            repo = Repository(vault, db, switches)
            assertFalse(repo.restore())
            assertEquals(scope, repo.session!!.scope)
            assertEquals(key, repo.pending!!.key)
            assertEquals(payment, repo.pending!!.body)
            switches.retry503 = true
            try {
                repo.submit()
                fail("503 accepted")
            } catch (_: ApiError) {}
            assertEquals(key, repo.pending!!.key)
            switches.corruptReceipt = true
            try {
                repo.submit()
                fail("malformed 2xx accepted")
            } catch (_: IllegalArgumentException) {}
            assertEquals(key, repo.pending!!.key)
            assertTrue(repo.pending!!.uncertain)
            repo.submit()
            assertNull(repo.pending)
            val after =
                repo.read("/api/v1/app/statements/$sid/summary") { Json.statement(JSONObject(it)) }
            assertEquals(before + "50.00".toBigDecimal(), after.data.paid.toBigDecimal())
            val listed =
                repo.read("/api/v1/app/statements/$sid/repayments?page=1&page_size=20") {
                    Json.page(it) { o -> Json.payment(o, false) }
                }
            assertEquals(1, listed.data.items.count { it.note == note })
            // Plans are independent, not paid money.
            repo.stage("/api/v1/app/statements/$sid/plans", payment, "计划还款")
            repo.submit()
            assertEquals(
                after.data.paid,
                repo
                    .read("/api/v1/app/statements/$sid/summary") { Json.statement(JSONObject(it)) }
                    .data
                    .paid,
            )
            val s = repo.session!!
            Api().call(s.server, "/api/v1/app/device/disconnect", s.token, "{}")
            try {
                repo.read("/api/v1/app/overview", Json::overview)
                fail("revoked credential used cache")
            } catch (e: ApiError) {
                assertEquals(401, e.status)
            }
            assertNull(repo.session)
            assertNull(vault.read("session"))
            assertNull(db.cache().get(scope + "/api/v1/app/overview"))
        } finally {
            vault.destroy()
            db.cache().clear()
            db.close()
        }
    }
}
