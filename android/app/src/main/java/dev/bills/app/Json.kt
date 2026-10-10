package dev.bills.app

import org.json.JSONObject

private fun JSONObject.text(key: String): String? =
    if (isNull(key) || !has(key)) null else getString(key)

private fun JSONObject.objects(key: String): List<JSONObject> =
    optJSONArray(key)?.let { a -> (0 until a.length()).map { a.getJSONObject(it) } } ?: emptyList()

object Json {
    fun account(o: JSONObject) =
        Account(
            o.getString("id"),
            o.getString("name"),
            o.getString("bank_name"),
            o.getString("cardholder_name"),
            o.getString("mode"),
            o.getBoolean("effective_active"),
            o.objects("cards").filter { it.getBoolean("active") }.map { it.getString("last_four") },
        )

    fun statement(o: JSONObject) =
        Statement(
            o.getString("id"),
            account(o.getJSONObject("account")),
            o.getString("statement_date"),
            o.getString("currency"),
            o.getString("status"),
            o.getInt("version"),
            o.text("amount"),
            o.text("minimum"),
            o.text("due_date"),
            o.getString("paid"),
            o.text("remaining"),
            o.getString("payment_status"),
            if (o.isNull("days_until_due")) null else o.getInt("days_until_due"),
            o.getBoolean("overdue"),
            o.getString("transaction_status"),
            o.text("period_start"),
            o.text("period_end"),
            o.getString("source_kind"),
        )

    fun overview(text: String): Overview {
        val o = JSONObject(text)
        return Overview(
            o.objects("totals").map { Total(it.getString("currency"), it.getString("amount")) },
            o.objects("latest_statements").map(::statement),
            o.objects("historical_unresolved").map(::statement),
            o.objects("warnings").map {
                Warning(
                    it.getString("account_id"),
                    it.getString("account_name"),
                    it.getString("message"),
                )
            },
            o.getInt("pending_count"),
            o.getString("disclaimer"),
            o.getString("as_of"),
        )
    }

    fun transaction(o: JSONObject) =
        Transaction(
            o.getString("description"),
            o.text("transaction_date"),
            o.text("posted_date"),
            o.text("amount"),
            o.text("currency"),
            o.getString("direction"),
            o.text("card_last_four"),
        )

    fun payment(o: JSONObject, plan: Boolean) =
        PaymentRecord(
            o.getString("id"),
            o.getString("amount"),
            o.getString("currency"),
            o.getString("occurred_at"),
            o.getString("note"),
            o.text(if (plan) "cancelled_at" else "revoked_at"),
            o.text(if (plan) "cancel_reason" else "revoke_reason"),
        )

    fun <T> page(text: String, decode: (JSONObject) -> T): Page<T> {
        val o = JSONObject(text)
        return Page(
            o.objects("items").map(decode),
            o.getInt("total"),
            o.getInt("page"),
            o.getInt("page_size"),
            if (o.has("version")) o.getInt("version") else null,
            o.text("transaction_status"),
        )
    }

    fun session(s: Session) =
        JSONObject()
            .put("server", s.server)
            .put("token", s.token)
            .put("id", s.id)
            .put("name", s.name)
            .put("expires", s.expiresAt)
            .put("scope", s.scope)
            .toString()

    fun session(text: String): Session {
        val o = JSONObject(text)
        return Session(
            o.getString("server"),
            o.getString("token"),
            o.getString("id"),
            o.getString("name"),
            o.getString("expires"),
            o.getString("scope"),
        )
    }

    fun pending(p: Pending) =
        JSONObject()
            .put("scope", p.scope)
            .put("key", p.key)
            .put("path", p.path)
            .put("body", p.body)
            .put("title", p.title)
            .put("confirm", p.needsConfirmation)
            .put("uncertain", p.uncertain)
            .toString()

    fun pending(text: String): Pending {
        val o = JSONObject(text)
        return Pending(
            o.getString("scope"),
            o.getString("key"),
            o.getString("path"),
            o.getString("body"),
            o.getString("title"),
            o.getBoolean("confirm"),
            o.getBoolean("uncertain"),
        )
    }
}
