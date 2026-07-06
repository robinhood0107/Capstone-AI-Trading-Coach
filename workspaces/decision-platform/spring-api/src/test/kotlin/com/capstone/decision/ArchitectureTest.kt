package com.capstone.decision

import com.tngtech.archunit.junit.AnalyzeClasses
import com.tngtech.archunit.junit.ArchTest
import com.tngtech.archunit.lang.ArchRule
import com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses

@AnalyzeClasses(packages = ["com.capstone.decision"])
class ArchitectureTest {
    companion object {
        @ArchTest
        @JvmField
        val domainDoesNotDependOnInfrastructure: ArchRule =
            noClasses()
                .that()
                .resideInAPackage("..domain..")
                .should()
                .dependOnClassesThat()
                .resideInAPackage("..infrastructure..")
                .allowEmptyShould(true)
    }
}
