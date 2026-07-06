package com.capstone.decision

import com.tngtech.archunit.junit.AnalyzeClasses
import com.tngtech.archunit.junit.ArchTest
import com.tngtech.archunit.lang.ArchRule
import com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses

// domain이 생기기 전부터 infrastructure 의존 금지 규칙을 CI에 걸어 경계 회귀를 막는다.
@AnalyzeClasses(packages = ["com.capstone.decision"])
class ArchitectureTest {
    companion object {
        // allowEmptyShould로 초기 skeleton에서도 규칙을 살려두고, domain 추가 시 즉시 검사하게 한다.
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
