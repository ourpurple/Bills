package dev.bills.app

import android.view.WindowManager
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class UiFlowTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test
    fun pairViewDetailsRecordAndPlanOnAndroid() {
        val f = fixture()
        compose.waitUntil(20000) {
            compose.onAllNodesWithText("HTTPS 服务地址").fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithText("HTTPS 服务地址").performTextInput("http://10.0.2.2:8014")
        compose.onNodeWithText("一次性配对码").performTextInput(f.getString("code"))
        compose.onNodeWithText("配对并连接").performScrollTo().performClick()
        compose.waitUntil(30000) {
            compose.onAllNodesWithText("全部待还").fetchSemanticsNodes().isNotEmpty()
        }
        val billTag = "bill-${f.getString("statement_id")}"
        compose.onNodeWithTag("screen-home").performScrollToNode(hasTestTag(billTag))
        compose.onNodeWithTag(billTag).performClick()
        val monthTag = "month-${f.getString("statement_id")}"
        compose.waitUntil(30000) {
            compose
                .onAllNodes(hasTestTag(monthTag) and isEnabled())
                .fetchSemanticsNodes()
                .isNotEmpty()
        }
        compose.onNodeWithTag("mark-payment").assertDoesNotExist()
        compose.onNodeWithTag(monthTag).performClick()
        compose.waitUntil(30000) {
            compose
                .onAllNodes(hasTestTag("details-more") and isEnabled())
                .fetchSemanticsNodes()
                .isNotEmpty()
        }
        compose.onNodeWithTag("details-more").performClick()
        compose.onNodeWithTag("open-statement").performClick()
        compose.waitUntil(30000) {
            compose.onAllNodes(hasText("标记已还款") and isEnabled()).fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithTag("mark-payment").performClick()
        compose.onNodeWithText("金额 / CNY").performTextReplacement("25")
        compose.onNodeWithText("在线提交").performClick()
        compose.waitUntil(30000) {
            compose
                .onAllNodes(hasText("服务端已确认记录", substring = true))
                .fetchSemanticsNodes()
                .isNotEmpty()
        }
        compose.waitUntil(30000) {
            compose.onAllNodes(hasText("计划还款") and isEnabled()).fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithText("计划还款").performClick()
        compose.onNodeWithText("金额 / CNY").performTextReplacement("40")
        compose.onNode(hasText("通知功能尚未实现", substring = true)).assertExists()
        compose.onNodeWithText("在线提交").performClick()
        compose.waitUntil(30000) {
            compose
                .onAllNodes(hasText("服务端已确认记录", substring = true))
                .fetchSemanticsNodes()
                .isNotEmpty()
        }
        compose.runOnUiThread {
            assertTrue(
                compose.activity.window.attributes.flags and
                    WindowManager.LayoutParams.FLAG_SECURE != 0
            )
        }
        compose.waitUntil(30000) {
            compose.onAllNodes(hasText("标记已还款") and isEnabled()).fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithTag("back").performClick()
        compose.waitUntil(30000) {
            compose
                .onAllNodes(hasTestTag("nav-settings") and isEnabled())
                .fetchSemanticsNodes()
                .isNotEmpty()
        }
        compose.onNodeWithTag("nav-settings").performClick()
        compose.onNodeWithText("断开连接 / 更换服务").performScrollTo().performClick()
        compose.onNodeWithText("确认清理并断开").performClick()
        compose.waitUntil(20000) {
            compose.onAllNodesWithText("配对并连接").fetchSemanticsNodes().isNotEmpty()
        }
    }
}
