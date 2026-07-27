package com.capstone.decision

import com.tngtech.archunit.core.domain.JavaClasses
import com.tngtech.archunit.core.importer.ClassFileImporter
import com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test

class PaperBrokerageArchitectureTest {
    @Test
    fun `paper application은 grpc infrastructure에 의존하지 않는다`() {
        noClasses()
            .that()
            .resideInAPackage("..application.brokerage.paper..")
            .should()
            .dependOnClassesThat()
            .resideInAPackage("..infrastructure.grpc..")
            .check(imported)
    }

    @Test
    fun `mock service는 paper 경로를 참조하지 않는다`() {
        noClasses()
            .that()
            .haveFullyQualifiedName("com.capstone.decision.application.brokerage.BrokerageService")
            .should()
            .dependOnClassesThat()
            .resideInAPackage("..brokerage.paper..")
            .check(imported)
    }

    @Test
    fun `paper 순수 domain은 Spring JDBC와 grpc를 모른다`() {
        noClasses()
            .that()
            .resideInAPackage("..domain.brokerage..")
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage("org.springframework..", "org.springframework.jdbc..", "io.grpc..")
            .check(imported)
    }

    companion object {
        private lateinit var imported: JavaClasses

        @JvmStatic
        @BeforeAll
        fun importApplicationClasses() {
            imported = ClassFileImporter().importPackages("com.capstone.decision")
        }
    }
}
