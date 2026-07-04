# AGV-TMS Scheduler Service (Java/Spring Boot)

多语言后端集成 — Java调度服务示例

## 项目结构

```
agvtms-scheduler-java/
├── pom.xml
├── README.md
├── src/main/java/com/agvtms/scheduler/
│   ├── AgvtmsSchedulerApplication.java      # 启动类
│   ├── config/
│   │   ├── KafkaConfig.java                 # Kafka配置
│   │   └── WebClientConfig.java            # HTTP客户端配置
│   ├── controller/
│   │   └── ScheduleController.java          # REST API
│   ├── service/
│   │   ├── SchedulerService.java            # 调度核心接口
│   │   └── impl/
│   │       └── MipSchedulerServiceImpl.java # MIP求解器实现
│   ├── model/
│   │   ├── TransportOrder.java              # 运输订单 (对应Proto)
│   │   ├── AgvStatus.java                   # AGV状态 (对应Proto)
│   │   ├── Assignment.java                  # 分配结果
│   │   └── ScheduleResponse.java           # 调度响应
│   ├── kafka/
│   │   ├── ScheduleRequestConsumer.java     # 消费调度请求
│   │   └── ScheduleResultProducer.java      # 发布调度结果
│   └── dto/                                 # 数据传输对象
├── src/test/java/
│   └── com/agvtms/scheduler/
│       └── SchedulerServiceTest.java        # 单元测试
└── src/main/resources/
    ├── application.yml                      # 配置文件
    └── bootstrap.yml                        # Spring Cloud配置(可选)
```

## 核心依赖 (pom.xml)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 
         https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.2.0</version>
        <relativePath/>
    </parent>
    
    <groupId>com.agvtms</groupId>
    <artifactId>agvtms-scheduler-java</artifactId>
    <version>1.0.0-SNAPSHOT</version>
    <name>AGV-TMS Scheduler Service</name>
    <description>Java-based scheduling service for AGV-TMS multi-language backend</description>

    <properties>
        <java.version>17</java.version>
        <protobuf.version>3.25.1</protobuf.version>
        <grpc.spring.version>2.15.0.RELEASE</grpc.spring.version>
    </properties>

    <dependencies>
        <!-- Spring Boot Starters -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-actuator</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-validation</artifactId>
        </dependency>
        
        <!-- Kafka -->
        <dependency>
            <groupId>org.springframework.kafka</groupId>
            <artifactId>spring-kafka</artifactId>
        </dependency>
        
        <!-- Protocol Buffers -->
        <dependency>
            <groupId>com.google.protobuf</groupId>
            <artifactId>protobuf-java</artifactId>
            <version>${protobuf.version}</version>
        </dependency>
        
        <!-- gRPC (可选, 用于高性能通信) -->
        <dependency>
            <groupId>net.devh</groupId>
            <artifactId>grpc-server-spring-boot-starter</artifactId>
            <version>${grpc.spring.version}</version>
        </dependency>
        
        <!-- MIP Solver (优化求解) -->
        <dependency>
            <groupId>com.google.ortools</groupId>
            <artifactId>ortools-java</artifactId>
            <version>9.7.2971</version>
        </dependency>
        
        <!-- Lombok -->
        <dependency>
            <groupId>org.projectlombok</groupId>
            <artifactId>lombok</artifactId>
            <optional>true</optional>
        </dependency>
        
        <!-- Test -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-test</artifactId>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>org.springframework.kafka</groupId>
            <artifactId>spring-kafka-test</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
                <configuration>
                    <excludes>
                        <exclude>
                            <groupId>org.projectlombok</groupId>
                            <artifactId>lombok</artifactId>
                        </exclude>
                    </excludes>
                </configuration>
            </plugin>
            
            <!-- Protobuf Compiler Plugin -->
            <plugin>
                <groupId>org.xolstice.maven.plugins</groupId>
                <artifactId>protobuf-maven-plugin</artifactId>
                <version>0.6.1</version>
                <configuration>
                    <protocArtifact>com.google.protobuf:protoc:${protobuf.version}:exe:${os.detected.classifier}</protocArtifact>
                    <pluginId>grpc-java</pluginId>
                    <grpcArtifact>io.grpc:protoc-gen-grpc-java:1.59.1:exe:${os.detected.classifier}</grpcArtifact>
                    <protoSourceRoot>${project.basedir}/src/main/proto</protoSourceRoot>
                </configuration>
                <executions>
                    <execution>
                        <goals>
                            <goal>compile</goal>
                            <goal>compile-custom</goal>
                        </goals>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
```

## 核心代码示例

### 1. 调度服务接口与实现

```java
// src/main/java/com/agvtms/scheduler/service/SchedulerService.java

package com.agvtms.scheduler.service;

import com.agvtms.scheduler.model.*;
import java.util.List;

/**
 * 调度核心服务接口.
 * 对应 Python 后端 schedule_service.py 的功能.
 */
public interface SchedulerService {
    
    /**
     * 执行调度计算.
     *
     * @param orders 待分配的运输订单列表
     * @param vehicles 可用AGV车辆列表
     * @return 调度结果 (包含分配方案和路径)
     */
    ScheduleResponse schedule(List<TransportOrder> orders, List<AgvStatus> vehicles);
    
    /**
     * 重新规划单个任务的路径.
     *
     * @param taskId 任务ID
     * @param currentPosition 当前位置
     * @param destination 目标位置
     * @return 新路径
     */
    List<Waypoint> replanPath(String taskId, Position currentPosition, Position destination);
    
    /**
     * 取消任务并重新调度受影响的订单.
     *
     * @param taskId 被取消的任务ID
     * @return 受影响订单的新分配方案
     */
    List<Assignment> cancelAndReschedule(String taskId);
}
```

### 2. MIP 求解器实现 (使用 OR-Tools)

```java
// src/main/java/com/agvtms/scheduler/service/impl/MipSchedulerServiceImpl.java

package com.agvtms.scheduler.service.impl;

import com.agvtms.scheduler.model.*;
import com.agvtms.scheduler.service.SchedulerService;
import com.google.ort.Loader;
import com.google.ort.linearsolver.MpConstraint;
import com.google.ort.linearsolver.MpObjective;
import com.google.ort.linearsolver.MpSolver;
import com.google.ort.linearsolver.MpVariable;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.stream.Collectors;

/**
 * 基于MIP (Mixed Integer Programming) 的调度实现.
 * 使用 Google OR-Tools 进行优化求解.
 *
 * 对应 Python backend/app/algorithms/v2/core/hybrid_scheduler.py
 */
@Slf4j
@Service
public class MipSolverSchedulerServiceImpl implements SchedulerService {
    
    private static final double SOLVER_TIME_LIMIT_SECONDS = 30.0;
    private static final double GAP_TOLERANCE = 0.02; // 2% gap
    
    @Override
    public ScheduleResponse schedule(List<TransportOrder> orders, List<AgvStatus> vehicles) {
        long startTime = System.currentTimeMillis();
        log.info("Starting MIP scheduler: {} orders, {} vehicles", orders.size(), vehicles.size());
        
        try {
            // Step 1: 构建MIP模型
            MpSolver solver = new MpSolver("AgvTmsScheduler");
            solver.setTimeLimit(SOLVER_TIME_LIMIT_SECONDS * 1000); // ms
            
            // 决策变量: x[order_idx][vehicle_idx] ∈ {0, 1}
            MpVariable[][] x = new MpVariable[orders.size()][vehicles.size()];
            for (int i = 0; i < orders.size(); i++) {
                for (int j = 0; j < vehicles.size(); j++) {
                    x[i][j] = solver.makeIntVar(0, 1, "x_" + i + "_" + j);
                }
            }
            
            // 约束1: 每个订单必须且只能分配给一辆车
            for (int i = 0; i < orders.size(); i++) {
                MpConstraint orderConstraint = solver.makeConstraint(1, 1);
                for (int j = 0; j < vehicles.size(); j++) {
                    orderConstraint.setCoefficient(x[i][j], 1);
                }
            }
            
            // 约束2: 车辆容量约束 (每辆车同时处理的订单数上限)
            int maxOrdersPerVehicle = 3; // 可配置
            for (int j = 0; j < vehicles.size(); j++) {
                MpConstraint capacityConstraint = solver.makeConstraint(0, maxOrdersPerVehicle);
                for (int i = 0; i < orders.size(); i++) {
                    capacityConstraint.setCoefficient(x[i][j], 1);
                }
            }
            
            // 目标函数: 最小化总成本 (距离 + 时间 + 能耗)
            MpObjective objective = solver.objective();
            objective.setMinimization();
            
            double[][] costMatrix = computeCostMatrix(orders, vehicles);
            for (int i = 0; i < orders.size(); i++) {
                for (int j = 0; j < vehicles.size(); j++) {
                    objective.setCoefficient(x[i][j], costMatrix[i][j]);
                }
            }
            
            // Step 2: 求解
            MpSolver.ResultStatus resultStatus = solver.solve();
            
            long computationTime = System.currentTimeMillis() - startTime;
            
            if (resultStatus == MpSolver.ResultStatus.OPTIMAL || 
                resultStatus == MpSolver.ResultStatus.FEASIBLE) {
                
                // 提取解
                List<Assignment> assignments = new ArrayList<>();
                Set<Integer> unassignedOrders = new HashSet<>();
                
                for (int i = 0; i < orders.size(); i++) {
                    boolean assigned = false;
                    for (int j = 0; j < vehicles.size(); j++) {
                        if (x[i][j].solutionValue() > 0.5) { // 整数解判断
                            Assignment assignment = buildAssignment(
                                orders.get(i), vehicles.get(j), costMatrix[i][j]
                            );
                            assignments.add(assignment);
                            assigned = true;
                            break;
                        }
                    }
                    if (!assigned) {
                        unassignedOrders.add(i);
                    }
                }
                
                log.info(
                    "Schedule completed: {} assigned, {} unassigned, time={}ms",
                    assignments.size(), unassignedOrders.size(), computationTime
                );
                
                return ScheduleResponse.builder()
                    .success(true)
                    .assignments(assignments)
                    .unassignedOrders(buildUnassignedList(orders, unassignedOrders))
                    .stats(ScheduleStatistics.builder()
                        .computationTimeMs(computationTime)
                        .totalOrders(orders.size())
                        .assignedOrders(assignments.size())
                        .unassignedOrders(unassignedOrders.size())
                        .algorithmUsed("mip-or-tools")
                        .build())
                    .build();
                    
            } else {
                log.warn("No feasible solution found, status={}", resultStatus);
                return ScheduleResponse.builder()
                    .success(false)
                    .errorMessage("No feasible solution: " + resultStatus)
                    .stats(ScheduleStatistics.builder()
                        .computationTimeMs(computationTime)
                        .algorithmUsed("mip-or-tools")
                        .build())
                    .build();
            }
            
        } catch (Exception e) {
            log.error("MIP solver error", e);
            return ScheduleResponse.builder()
                .success(false)
                .errorMessage(e.getMessage())
                .build();
        }
    }
    
    private double[][] computeCostMatrix(List<TransportOrder> orders, List<AgvStatus> vehicles) {
        // 计算每个订单-车辆对的成本 (距离 + 时间惩罚 + 电量惩罚)
        double[][] costs = new double[orders.size()][vehicles.size()];
        
        for (int i = 0; i < orders.size(); i++) {
            TransportOrder order = orders.get(i);
            for (int j = 0; j < vehicles.size(); j++) {
                AgvStatus vehicle = vehicles.get(j);
                
                // 基础距离成本
                double distance = calculateDistance(
                    vehicle.getPosition(), order.getSource(), order.getDestination()
                );
                
                // 时间成本
                double timeCost = distance / Math.max(vehicle.getSpeed(), 0.1);
                
                // 低电量惩罚
                double batteryPenalty = vehicle.getBatteryLevel() < 20.0 ? 100.0 : 0.0;
                
                // 状态惩罚 (故障车辆不可用)
                double statusPenalty = (vehicle.getState() == AgvState.AGV_ERROR) 
                    ? Double.MAX_VALUE : 0.0;
                
                costs[i][j] = distance * 10 + timeCost + batteryPenalty + statusPenalty;
            }
        }
        return costs;
    }
    
    private double calculateDistance(Position from, Location source, Location dest) {
        // 简化的曼哈顿距离 + 欧几里得混合
        double d1 = Math.sqrt(
            Math.pow(source.getPosition().getX() - from.getX(), 2) +
            Math.pow(source.getPosition().getY() - from.getY(), 2)
        );
        double d2 = Math.sqrt(
            Math.pow(dest.getPosition().getX() - source.getPosition().getX(), 2) +
            Math.pow(dest.getPosition().getY() - source.getPosition().getY(), 2)
        );
        return d1 + d2;
    }
    
    // ... 其他辅助方法省略 ...
}
```

### 3. Kafka 消费者 (接收调度请求)

```java
// src/main/java/com/agvtms/scheduler/kafka/ScheduleRequestConsumer.java

package com.agvtms.scheduler.kafka;

import com.agvtms.scheduler.model.*;
import com.agvtms.scheduler.service.SchedulerService;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Kafka消费者 — 接收来自Python后端的调度请求.
 *
 * 监听 Topic: agvtms.schedule.requested.v1
 * 格式: CloudEvents 1.0 (JSON)
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class ScheduleRequestConsumer {
    
    private final SchedulerService schedulerService;
    private final ScheduleResultProducer resultProducer;
    private final ObjectMapper objectMapper;
    
    /**
     * 消费调度请求事件.
     *
     * CloudEvent 格式:
     * {
     *   "specversion": "1.0",
     *   "type": "agvtms.schedule.requested.v1",
     *   "source": "/agvtms/python",
     *   "data": { "order_ids": [...], "options": {...} }
     * }
     */
    @KafkaListener(
        topics = "${kafka.topics.schedule-request:agvtms.schedule.requested.v1}",
        groupId = "${kafka.consumer.group:scheduler-service}",
        concurrency = "${kafka.consumer.concurrency:2}"
    )
    public void onScheduleRequest(ConsumerRecord<String, String> record) {
        log.info(
            "Received schedule request: partition={}, offset={}, key={}",
            record.partition(), record.offset(), record.key()
        );
        
        try {
            // 解析 CloudEvent
            CloudEventWrapper event = objectMapper.readValue(
                record.value(), CloudEventWrapper.class
            );
            
            // 解析业务数据
            @SuppressWarnings("unchecked")
            Map<String, Object> data = objectMapper.convertValue(
                event.getData(), Map.class
            );
            
            // TODO: 从数据库或API获取完整的订单和车辆信息
            // 这里简化处理，实际需要调用其他服务或查询DB
            List<String> orderIds = (List<String>) data.get("order_ids");
            
            // 执行调度
            ScheduleResponse response = schedulerService.schedule(
                Collections.emptyList(), // 实际从DB查询
                Collections.emptyList()
            );
            
            // 发布结果到 Kafka
            resultProducer.publishScheduleResult(event.getId(), response);
            
        } catch (Exception e) {
            log.error("Failed to process schedule request", e);
            throw new RuntimeException("Schedule processing failed", e);
        }
    }
}
```

### 4. 配置文件

```yaml
# src/main/resources/application.yml

server:
  port: 8080

spring:
  application:
    name: agvtms-scheduler-java
  
  kafka:
    bootstrap-servers: ${KAFKA_BOOTSTRAP_SERVERS:kafka:9092}
    consumer:
      group-id: scheduler-service
      auto-offset-reset: latest
      enable-auto-commit: false
      key-deserializer: org.apache.kafka.common.serialization.StringDeserializer
      value-deserializer: org.apache.kafka.common.serialization.StringDeserializer
    producer:
      key-serializer: org.apache.kafka.common.serialization.StringSerializer
      value-serializer: org.apache.kafka.common.serialization.StringSerializer
      properties:
        acks: all
        retries: 3
        linger.ms: 5

# 自定义配置
agvtms:
  scheduler:
    algorithm: mip
    time-limit-seconds: 30
    gap-tolerance: 0.02
    
# Actuator
management:
  endpoints:
    web:
      exposure:
        include: health,info,prometheus
  endpoint:
    health:
      show-details: always

logging:
  level:
    com.agvtms.scheduler: DEBUG
    org.apache.kafka: WARN
```

## 与Python后端的集成方式

```
┌─────────────────────┐         Kafka          ┌──────────────────────────┐
│   Python FastAPI     │ ◄═════════════════► │  Java Scheduler Service   │
│                     │                       │                          │
│  发送调度请求 →       │ agvtms.schedule.      │ 接收请求 →               │
│  topic: requested    │ requested.v1          │ MIP求解 →               │
│                     │                       │ 发布结果 →               │
│  接收调度结果 ←       │ agvtms.schedule.      │ topic: result            │
│  topic: result       │ result.v1             │                          │
└─────────────────────┘                       └──────────────────────────┘
```

**关键点**:
1. 通过 **Kafka** 异步通信 (不直接HTTP调用)
2. 使用 **CloudEvents 1.0** 标准格式保证兼容性
3. 共享 **Proto 定义** 确保数据模型一致
4. Java专注 **优化求解**, Python负责其他业务逻辑
