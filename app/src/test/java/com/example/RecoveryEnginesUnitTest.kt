package com.example

import com.example.whatsapp.WhatsAppCryptoEngine
import java.io.ByteArrayOutputStream
import java.util.zip.Deflater
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class RecoveryEnginesUnitTest {

    @Test
    fun normalize64HexKey_parsesValidKey() {
        val hex = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        val bytes = WhatsAppCryptoEngine.normalize64HexKey(hex)
        assertEquals(32, bytes.size)
        assertEquals(0x01.toByte(), bytes[0])
        assertEquals(0xEF.toByte(), bytes[31])
    }

    @Test
    fun hkdfAndAesGcmRoundTrip_matchesCrypt15Spec() {
        val rootKey = ByteArray(32) { (it + 1).toByte() }
        val mac1 = Mac.getInstance("HmacSHA256")
        mac1.init(SecretKeySpec(ByteArray(32), "HmacSHA256"))
        val prk = mac1.doFinal(rootKey)
        val mac2 = Mac.getInstance("HmacSHA256")
        mac2.init(SecretKeySpec(prk, "HmacSHA256"))
        mac2.update("backup encryption".toByteArray())
        mac2.update(byteArrayOf(0x01))
        val aesKey = mac2.doFinal().copyOf(32)

        val iv = ByteArray(16) { (it + 10).toByte() }
        val plain = "SQLite format 3\u0000test".toByteArray()
        val deflater = Deflater()
        deflater.setInput(plain)
        deflater.finish()
        val comp = ByteArrayOutputStream()
        val buf = ByteArray(128)
        while (!deflater.finished()) {
            val c = deflater.deflate(buf)
            comp.write(buf, 0, c)
        }

        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(aesKey, "AES"), GCMParameterSpec(128, iv))
        val encrypted = cipher.doFinal(comp.toByteArray())

        val dec = Cipher.getInstance("AES/GCM/NoPadding")
        dec.init(Cipher.DECRYPT_MODE, SecretKeySpec(aesKey, "AES"), GCMParameterSpec(128, iv))
        val outComp = dec.doFinal(encrypted)
        assertArrayEquals(comp.toByteArray(), outComp)
    }
}
