package com.capstone.decision.infrastructure.dashboard

import com.capstone.decision.application.dashboard.DashboardViewPort
import com.capstone.decision.application.dashboard.DashboardViewService
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration

@Configuration
class DashboardConfiguration {
    @Bean
    fun dashboardViewService(port: DashboardViewPort) = DashboardViewService(port)
}
