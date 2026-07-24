package com.capstone.decision

import com.capstone.decision.contract.v1.DisclosureObservationContract
import com.google.protobuf.DescriptorProtos.FileDescriptorSet
import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.HexFormat

// Java와 Python이 같은 tracked descriptor를 소비하는지 검증해 field number drift를 차단한다.
class DisclosureProtoParityTest {
    @Test
    fun `generated Java descriptor equals tracked compatibility descriptor`() {
        val root = findRepositoryRoot()
        val descriptorBytes =
            Files.readAllBytes(
                root.resolve("contracts/proto/disclosure_observation.descriptor.pb"),
            )
        val descriptorSet = FileDescriptorSet.parseFrom(descriptorBytes)
        val expectedHash =
            Files
                .readString(
                    root.resolve("contracts/proto/disclosure_observation.descriptor.sha256"),
                ).trim()

        assertEquals(1, descriptorSet.fileCount)
        assertArrayEquals(
            descriptorSet.getFile(0).toByteArray(),
            DisclosureObservationContract.getDescriptor().toProto().toByteArray(),
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
