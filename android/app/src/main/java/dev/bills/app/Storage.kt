package dev.bills.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import androidx.room.*
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class Vault(context: Context, private val alias: String = "bills-private-v1") {
    private val dir = File(context.noBackupFilesDir, "private").apply { mkdirs() }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let {
            return it
        }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            .apply {
                init(
                    KeyGenParameterSpec.Builder(
                            alias,
                            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                        )
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .setRandomizedEncryptionRequired(true)
                        .build()
                )
            }
            .generateKey()
    }

    fun encrypt(text: String): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, key())
        return c.iv + c.doFinal(text.toByteArray(Charsets.UTF_8))
    }

    fun decrypt(bytes: ByteArray): String {
        require(bytes.size >= 28)
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12)))
        return c.doFinal(bytes.copyOfRange(12, bytes.size)).toString(Charsets.UTF_8)
    }

    fun read(name: String): String? {
        val file = AtomicFile(File(dir, name))
        return try {
            file.openRead().use { decrypt(it.readBytes()) }
        } catch (_: java.io.FileNotFoundException) {
            null
        }
    }

    fun write(name: String, text: String) {
        val bytes = encrypt(text)
        val file = AtomicFile(File(dir, name))
        val stream = file.startWrite()
        try {
            stream.write(bytes)
            file.finishWrite(stream)
        } catch (e: Exception) {
            file.failWrite(stream)
            throw e
        }
    }

    fun remove(name: String) = AtomicFile(File(dir, name)).delete()

    fun destroy() {
        remove("session")
        remove("pending")
        KeyStore.getInstance("AndroidKeyStore").apply {
            load(null)
            deleteEntry(alias)
        }
    }
}

@Entity(tableName = "cache")
data class CacheRow(@PrimaryKey val key: String, val payload: ByteArray, val at: Long)

@Dao
interface CacheDao {
    @Query("SELECT * FROM cache WHERE `key` = :key") suspend fun get(key: String): CacheRow?

    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun put(row: CacheRow)

    @Query("DELETE FROM cache") suspend fun clear()

    @Query(
        "DELETE FROM cache WHERE `key` NOT IN (SELECT `key` FROM cache ORDER BY at DESC LIMIT 200)"
    )
    suspend fun prune()
}

@Database(entities = [CacheRow::class], version = 1, exportSchema = true)
abstract class CacheDb : RoomDatabase() {
    abstract fun cache(): CacheDao

    companion object {
        fun open(context: Context) =
            Room.databaseBuilder(
                    context,
                    CacheDb::class.java,
                    File(context.noBackupFilesDir, "bills-cache.db").absolutePath,
                )
                .build()
    }
}
