package com.capstone.decision

import com.capstone.decision.contract.v1.RagContract
import com.google.protobuf.DescriptorProtos.FileDescriptorSet
import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.HexFormat

// Java와 Python이 같은 canonical RAG descriptor를 소비해 request/response field drift를 차단한다.
class RagProtoParityTest {
    @Test
    fun `generated Java RAG descriptor equals tracked compatibility descriptor`() {
        val root = findRepositoryRoot()
        val descriptorBytes = Files.readAllBytes(root.resolve("contracts/proto/rag.descriptor.pb"))
        val descriptorSet = FileDescriptorSet.parseFrom(descriptorBytes)
        val expectedHash = Files.readString(root.resolve("contracts/proto/rag.descriptor.sha256")).trim()

        assertEquals(1, descriptorSet.fileCount)
        assertArrayEquals(
            descriptorSet.getFile(0).toByteArray(),
            RagContract.getDescriptor().toProto().toByteArray(),
        )
        assertEquals(
            expectedHash,
            HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(descriptorBytes)),
        )
    }

    private fun findRepositoryRoot(): Path {
        var current = Path.of(System.getProperty("user.dir")).toAbsolutePath()
        while (!Files.exists(current.resolve("AGENTS.md"))) {
            current = current.parent ?: error("repository root was not found")
        }
        return current
    }
}
