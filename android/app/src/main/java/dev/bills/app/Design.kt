package dev.bills.app

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

internal val Ink = Color(0xFF292929)
internal val Muted = Color(0xFF99999F)
internal val Line = Color(0xFFF0F0F3)
internal val Soft = Color(0xFFF8F8FA)
internal val Coral = Color(0xFFFF626B)
internal val Blue = Color(0xFF389ED0)
internal val Yellow = Color(0xFFFFBE0B)
internal val Header = Color(0xFF888C9F)

@Composable
internal fun BillsTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme =
            lightColorScheme(
                primary = Blue,
                onPrimary = Color.White,
                secondary = Yellow,
                background = Color.White,
                surface = Color.White,
                onSurface = Ink,
                onBackground = Ink,
                outline = Color(0xFFDDDEE3),
                error = Color(0xFFD94755),
                surfaceContainer = Soft,
                surfaceContainerHighest = Soft,
            ),
        content = content,
    )
}

@Composable
internal fun AppIcon(kind: String, color: Color = Ink, modifier: Modifier = Modifier.size(24.dp)) {
    Canvas(modifier) {
        val u = size.minDimension / 24f
        fun p(x: Float, y: Float) = Offset(x * u, y * u)
        fun line(x: Float, y: Float, x2: Float, y2: Float) =
            drawLine(color, p(x, y), p(x2, y2), 1.8f * u, StrokeCap.Round)
        when (kind) {
            "bills" -> {
                drawRoundRect(
                    color,
                    p(3f, 4f),
                    Size(18f * u, 7f * u),
                    androidx.compose.ui.geometry.CornerRadius(u),
                )
                drawRoundRect(
                    color,
                    p(3f, 13f),
                    Size(18f * u, 7f * u),
                    androidx.compose.ui.geometry.CornerRadius(u),
                )
            }
            "details" -> {
                drawRoundRect(
                    color,
                    p(4f, 2f),
                    Size(16f * u, 20f * u),
                    androidx.compose.ui.geometry.CornerRadius(2f * u),
                    style = Stroke(1.7f * u),
                )
                line(8f, 7f, 16f, 7f)
                line(8f, 12f, 16f, 12f)
                line(8f, 17f, 13f, 17f)
            }
            "settings",
            "filter" -> {
                line(3f, 6f, 21f, 6f)
                line(3f, 12f, 21f, 12f)
                line(3f, 18f, 21f, 18f)
                listOf(p(8f, 6f), p(16f, 12f), p(10f, 18f)).forEach {
                    drawCircle(Color.White, 2.6f * u, it)
                    drawCircle(color, 2.6f * u, it, style = Stroke(1.7f * u))
                }
            }
            "back" -> {
                line(15f, 4f, 7f, 12f)
                line(7f, 12f, 15f, 20f)
            }
            "next" -> {
                line(9f, 5f, 16f, 12f)
                line(16f, 12f, 9f, 19f)
            }
            "down",
            "up" -> {
                val path =
                    Path().apply {
                        if (kind == "down") {
                            moveTo(6 * u, 9 * u)
                            lineTo(18 * u, 9 * u)
                            lineTo(12 * u, 15 * u)
                        } else {
                            moveTo(6 * u, 15 * u)
                            lineTo(18 * u, 15 * u)
                            lineTo(12 * u, 9 * u)
                        }
                        close()
                    }
                drawPath(path, color)
            }
            "more" -> listOf(5f, 12f, 19f).forEach { drawCircle(color, 1.8f * u, p(it, 12f)) }
            "refresh" -> {
                drawArc(
                    color,
                    -50f,
                    290f,
                    false,
                    p(4f, 4f),
                    Size(16 * u, 16 * u),
                    style = Stroke(1.8f * u),
                )
                line(20f, 3f, 20f, 9f)
                line(15f, 9f, 20f, 9f)
            }
            "eye" -> {
                val path =
                    Path().apply {
                        moveTo(2 * u, 12 * u)
                        cubicTo(7 * u, 3 * u, 17 * u, 3 * u, 22 * u, 12 * u)
                        cubicTo(17 * u, 21 * u, 7 * u, 21 * u, 2 * u, 12 * u)
                    }
                drawPath(path, color, style = Stroke(1.7f * u))
                drawCircle(color, 3 * u, p(12f, 12f), style = Stroke(1.7f * u))
            }
            "clock" -> {
                drawCircle(color, 9 * u, p(12f, 12f), style = Stroke(1.7f * u))
                line(12f, 6f, 12f, 12f)
                line(12f, 12f, 16f, 14f)
            }
            "check" -> {
                line(5f, 12f, 10f, 17f)
                line(10f, 17f, 20f, 6f)
            }
            else -> {
                drawCircle(color, 9 * u, p(12f, 12f), style = Stroke(1.7f * u))
                line(7f, 12f, 17f, 12f)
            }
        }
    }
}

@Composable
internal fun BankBadge(bank: String, modifier: Modifier = Modifier) {
    val tint =
        when {
            "农业" in bank || "邮" in bank -> Color(0xFF20B699)
            "交通" in bank || "建设" in bank || "兴业" in bank -> Color(0xFF3978B8)
            "平安" in bank -> Color(0xFFF08B36)
            else -> Color(0xFFE85865)
        }
    Box(
        modifier.size(25.dp).background(tint, CircleShape).semantics { contentDescription = bank },
        contentAlignment = Alignment.Center,
    ) {
        Text(
            shortBankName(bank).take(1),
            color = Color.White,
            fontSize = 15.sp,
            fontWeight = FontWeight.Bold,
        )
    }
}

@Composable
internal fun Notice(text: String, modifier: Modifier = Modifier) {
    Surface(modifier, color = Soft, shape = MaterialTheme.shapes.small) {
        Text(
            text,
            Modifier.padding(10.dp),
            color = Muted,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
internal fun EmptyState(
    title: String,
    message: String,
    retry: (() -> Unit)? = null,
    busy: Boolean = false,
) {
    Column(
        Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        AppIcon("details", Color(0xFFD3D4DB), Modifier.size(42.dp))
        Text(title, fontSize = 18.sp, color = Ink)
        Text(message, color = Muted, style = MaterialTheme.typography.bodyMedium)
        if (retry != null) TextButton(onClick = retry, enabled = !busy) { Text("重新读取") }
    }
}
