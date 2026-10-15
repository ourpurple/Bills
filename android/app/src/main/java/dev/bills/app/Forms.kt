package dev.bills.app

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import java.time.ZonedDateTime
import org.json.JSONObject

@Composable
internal fun PendingCard(
    p: Pending,
    busy: Boolean,
    retry: () -> Unit,
    confirm: () -> Unit,
    cancel: () -> Unit,
) {
    Card {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val body = remember(p.body) { JSONObject(p.body) }
            if (body.has("amount"))
                Text(
                    "${money(body.getString("amount"),body.getString("currency"))} · ${shownTime(body.getString("occurred_at"))}（北京时间）\n${body.optString("note")}"
                )
            if (body.has("reason")) Text("纠错原因：${body.getString("reason")}")
            Text(
                "${p.title} · ${if(p.needsConfirmation) "需要再次确认" else "尚未确认结果"}",
                fontWeight = FontWeight.Bold,
            )
            Text(
                if (p.needsConfirmation) "服务端要求核对超额金额或出账前时间。确认后仍不执行支付。"
                else "可能已写入，不能另建重复记录。重试沿用原内容和同一提交标识；关闭 App 后仍可手动核对重试。"
            )
            if (p.needsConfirmation) {
                Button(onClick = confirm, enabled = !busy) { Text("已核对，确认异常并提交") }
                TextButton(onClick = cancel, enabled = !busy) { Text("放弃本次未通过校验的提交") }
            } else Button(onClick = retry, enabled = !busy) { Text("核对重试（不新建）") }
        }
    }
}

@Composable
internal fun PaymentDialog(
    currency: String,
    plan: Boolean,
    busy: Boolean,
    dismiss: () -> Unit,
    submit: (String, String, String) -> Unit,
) {
    var amount by remember { mutableStateOf("") }
    var time by remember {
        mutableStateOf(
            ZonedDateTime.now(Rules.zone).plusDays(if (plan) 1 else 0).format(Rules.dateTime)
        )
    }
    var note by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    AlertDialog(
        onDismissRequest = { if (!busy) dismiss() },
        title = { Text(if (plan) "保存计划（不是已还款）" else "记录还款（不执行支付）") },
        text = {
            Column(
                Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(if (plan) "计划不减少待还金额。通知功能尚未实现，保存后不会安排提醒。" else "请填写实际还款金额与时间，仅作个人标记，不证明银行到账。")
                OutlinedTextField(
                    amount,
                    { amount = it },
                    label = { Text("金额 / $currency") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                )
                OutlinedTextField(
                    time,
                    { time = it },
                    label = { Text("北京时间 yyyy-MM-dd HH:mm") },
                    singleLine = true,
                )
                OutlinedTextField(
                    note,
                    { note = it.take(500) },
                    label = { Text("备注（可选）") },
                    maxLines = 3,
                )
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    try {
                        Rules.amount(amount, currency)
                        Rules.instant(time)
                        submit(amount, time, note)
                    } catch (e: IllegalArgumentException) {
                        error = e.message
                    }
                },
                enabled = !busy,
            ) {
                Text("在线提交")
            }
        },
        dismissButton = { TextButton(onClick = dismiss, enabled = !busy) { Text("取消") } },
    )
}

@Composable
internal fun ReasonDialog(title: String, dismiss: () -> Unit, submit: (String) -> Unit) {
    var reason by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = dismiss,
        title = { Text(title) },
        text = {
            Column {
                Text("不会撤销真实银行转账。原记录将保留以供核对。")
                OutlinedTextField(reason, { reason = it.take(500) }, label = { Text("请填写原因") })
            }
        },
        confirmButton = {
            TextButton(onClick = { submit(reason) }, enabled = reason.isNotBlank()) {
                Text("确认并在线提交")
            }
        },
        dismissButton = { TextButton(onClick = dismiss) { Text("返回") } },
    )
}

@Composable
internal fun <T> Pager(page: Page<T>, busy: Boolean, go: (Int) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        TextButton(onClick = { go(page.page - 1) }, enabled = !busy && page.page > 1) {
            Text("上一页")
        }
        Text(
            "第 ${page.page} 页 / 共 ${page.total} 条",
            Modifier.padding(top = 12.dp),
            style = MaterialTheme.typography.bodySmall,
        )
        TextButton(onClick = { go(page.page + 1) }, enabled = !busy && page.hasNext) { Text("下一页") }
    }
}
