package dev.bills.app

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
internal fun SettingsScreen(s: UiState, actions: UiActions, defaultPicker: () -> Unit) {
    var disconnectOpen by remember { mutableStateOf(false) }
    Column {
        SettingsGroup("连接与设备") {
            SettingsValue("设备名称", s.connection?.name ?: "未配对")
            SettingsValue("服务地址", s.connection?.server ?: "未连接")
            SettingsValue("授权有效期", s.connection?.expiresAt?.let(::shownTime) ?: "未知")
            SettingsValue("读取状态", if (s.cached) "缓存 · 非最新 · 离线只读" else "在线读取")
        }
        SettingsGroup("显示偏好 · 仅保存在本机") {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("隐藏金额", fontSize = 15.sp)
                    Text("账单、交易和还款记录统一隐藏", color = Muted, fontSize = 12.sp)
                }
                Switch(checked = s.amountsHidden, onCheckedChange = actions.hideAmounts)
            }
            HorizontalDivider(color = Line, modifier = Modifier.padding(horizontal = 16.dp))
            val default =
                s.accounts?.items?.find { it.id == s.defaultAccountId }
                    ?: s.selectedAccount?.takeIf { it.id == s.defaultAccountId }
            Row(
                Modifier.fillMaxWidth()
                    .clickable(enabled = !s.busy) { defaultPicker() }
                    .padding(horizontal = 16.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text("默认详单账户", fontSize = 15.sp)
                    Text(
                        default?.let { "${it.bankName} · ${it.holderName} · ${accountTails(it)}" }
                            ?: if (s.defaultAccountId == null) "首次进入自动选择；随后保留当前账户"
                            else "已保存的账户（可重新选择）",
                        color = Muted,
                        fontSize = 12.sp,
                    )
                }
                AppIcon("next", Muted, Modifier.size(18.dp))
            }
        }
        SettingsGroup("数据状态") {
            SettingsValue("手机同步时间", stamp(s.cacheAt) + "（北京时间）")
            SettingsValue("待核对提交", s.pending?.title ?: "无")
            Text(
                "缓存最多保留 200 个页面，超过 30 天不再作为离线数据。只缓存已查看页面；离线不自动发送还款或计划。",
                color = Muted,
                fontSize = 12.sp,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
            )
        }
        SettingsGroup("使用说明") {
            SettingsValue("还款标记", "不执行支付，不确认银行到账")
            SettingsValue("还款计划", "不减少待还；通知尚未实现")
            SettingsValue("更新账单", "读取已有数据，不触发收信或模型识别")
            SettingsValue("隐私保护", "凭证与缓存加密，禁止截屏和系统备份")
            SettingsValue("应用版本", BuildConfig.VERSION_NAME)
        }
        TextButton(
            onClick = { disconnectOpen = true },
            enabled = !s.busy,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        ) {
            Text("断开连接 / 更换服务", color = Coral)
        }
    }
    if (disconnectOpen)
        AlertDialog(
            onDismissRequest = { disconnectOpen = false },
            title = { Text("清理本机并断开？") },
            text = { Text("将清理本机凭证、账单缓存和待核对请求，并尝试撤销当前设备授权。不会删除服务器账单；若有待核对提交，请先在后台核对。") },
            confirmButton = {
                TextButton(
                    onClick = {
                        disconnectOpen = false
                        actions.disconnect()
                    },
                    enabled = !s.busy,
                ) {
                    Text("确认清理并断开", color = Coral)
                }
            },
            dismissButton = { TextButton(onClick = { disconnectOpen = false }) { Text("返回") } },
        )
}

@Composable
private fun SettingsGroup(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column {
        Surface(color = Soft, modifier = Modifier.fillMaxWidth()) {
            Text(
                title,
                color = Muted,
                fontSize = 12.sp,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            )
        }
        content()
    }
}

@Composable
private fun SettingsValue(title: String, value: String) {
    Column(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        Text(title, fontSize = 14.sp)
        Text(value, color = Muted, fontSize = 12.sp)
    }
    HorizontalDivider(color = Line, modifier = Modifier.padding(horizontal = 16.dp))
}

@Composable
internal fun AccountPicker(
    s: UiState,
    actions: UiActions,
    dismiss: () -> Unit,
    defaultMode: Boolean,
) {
    AlertDialog(
        onDismissRequest = dismiss,
        title = { Text(if (defaultMode) "默认详单账户" else "选择还款账户") },
        text = {
            Column {
                if (s.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                val page = s.accounts
                if (page == null) {
                    Text("账户尚未读取成功，请刷新。", color = Muted)
                    TextButton(onClick = { actions.accountsPage(1) }, enabled = !s.busy) {
                        Text("读取账户")
                    }
                } else {
                    LazyColumn(Modifier.heightIn(max = 360.dp)) {
                        if (defaultMode)
                            item {
                                TextButton(
                                    onClick = {
                                        actions.defaultAccount(null)
                                        dismiss()
                                    },
                                    enabled = !s.busy,
                                ) {
                                    Text("自动选择（清除默认账户）")
                                }
                            }
                        if (page.items.isEmpty())
                            item {
                                Text(
                                    "暂无账户，请在现有 Web 管理端添加。",
                                    color = Muted,
                                    modifier = Modifier.padding(vertical = 20.dp),
                                )
                            }
                        items(page.items, key = { it.id }) { a ->
                            Column(
                                Modifier.fillMaxWidth()
                                    .clickable(enabled = !s.busy) {
                                        if (defaultMode) actions.defaultAccount(a.id)
                                        else actions.account(a)
                                        dismiss()
                                    }
                                    .padding(vertical = 10.dp),
                                verticalArrangement = Arrangement.spacedBy(3.dp),
                            ) {
                                AccountIdentity(a)
                                Text(
                                    "${a.name} · ${if(a.mode=="merged") "合并还款" else "独立还款"}${if(!a.active) " · 已停用" else ""}",
                                    color = Muted,
                                    fontSize = 11.sp,
                                )
                                if (
                                    a.id ==
                                        (if (defaultMode) s.defaultAccountId
                                        else s.selectedAccount?.id)
                                )
                                    Text("当前选择", color = Blue, fontSize = 11.sp)
                            }
                            HorizontalDivider(color = Line)
                        }
                    }
                    Pager(page, s.busy, actions.accountsPage)
                }
            }
        },
        confirmButton = { TextButton(onClick = dismiss) { Text("关闭") } },
    )
}

@Composable
internal fun PairScreen(s: UiState, pair: (String, String, String) -> Unit) {
    var server by rememberSaveable { mutableStateOf("") }
    // Never put the one-time pairing secret in saved instance state.
    var code by remember { mutableStateOf("") }
    var name by rememberSaveable { mutableStateOf("我的手机") }
    LazyColumn(
        Modifier.fillMaxSize().systemBarsPadding().imePadding().padding(horizontal = 24.dp),
        contentPadding = PaddingValues(top = 24.dp, bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            AppIcon("bills", Blue, Modifier.size(34.dp))
            Spacer(Modifier.height(12.dp))
            Text("账序", fontSize = 34.sp, fontWeight = FontWeight.Bold)
            Text(
                "私人账单 · 清楚记录",
                color = Muted,
                fontSize = 14.sp,
                modifier = Modifier.padding(top = 8.dp),
            )
        }
        item { Notice("在现有 Web 管理端「安全设置」生成一次性配对码。手机仅查看账单、标记还款及保存计划，不管理邮箱或模型。") }
        item {
            OutlinedTextField(
                server,
                { server = it },
                label = { Text("HTTPS 服务地址") },
                placeholder = { Text("https://bills.example.com") },
                singleLine = true,
                enabled = !s.busy,
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
        }
        item {
            OutlinedTextField(
                code,
                { code = it },
                label = { Text("一次性配对码") },
                singleLine = true,
                enabled = !s.busy,
                modifier = Modifier.fillMaxWidth(),
                visualTransformation = PasswordVisualTransformation(),
            )
        }
        item {
            OutlinedTextField(
                name,
                { name = it.take(80) },
                label = { Text("设备名称") },
                singleLine = true,
                enabled = !s.busy,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        s.message?.let { item { Notice(it) } }
        item {
            Button(
                onClick = {
                    pair(server, code, name)
                    code = ""
                },
                enabled = !s.busy && server.isNotBlank() && code.isNotBlank() && name.isNotBlank(),
                modifier = Modifier.fillMaxWidth().heightIn(min = 50.dp),
            ) {
                Text(if (s.busy) "正在连接…" else "配对并连接")
            }
        }
        item {
            Text(
                "正式连接须使用有效 HTTPS 证书。凭证由手机安全存储保护；账单不代表银行实时余额，请以银行为准。",
                color = Muted,
                fontSize = 12.sp,
            )
        }
    }
}
