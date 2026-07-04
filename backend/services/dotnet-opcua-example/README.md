# AGV-TMS OPC-UA Adapter Service (.NET/ASP.NET Core)

多语言后端集成 — .NET工业协议适配服务示例

## 项目结构

```
agvtms-opcua-dotnet/
├── AgvTms.OpcUaAdapter.sln
├── src/
│   └── AgvTms.OpcUaAdapter/
│       ├── AgvTms.OpcUaAdapter.csproj
│       ├── Program.cs                    # 入口 (Minimal API)
│       ├── OpcUaConfig.cs                # 配置模型
│       ├── Services/
│       │   ├── IOpcUaService.cs          # 服务接口
│       │   ├── OpcUaService.cs           # OPC UA服务实现
│       │   ├── IProtocolAdapterManager.cs
│       │   └── ProtocolAdapterManager.cs  # 协议适配器管理
│       ├── Controllers/
│       │   ├── OpcUaController.cs         # REST API控制器
│       │   └── HealthCheckController.cs  # 健康检查
│       ├── Models/
│       │   ├── OpcUaNode.cs              // 节点模型
│       │   ├── SubscriptionData.cs      // 订阅数据
│       │   └── AgvTmsCommon.proto        // 共享Proto定义
│       ├── Kafka/
│       │   ├── IKafkaProducer.cs
│       │   ├── KafkaEventProducer.cs     // 事件发布
│       │   └── KafkaEventConsumer.cs     // 事件消费
│       └── Middleware/
│           └── RequestLoggingMiddleware.cs
├── tests/
│   └── AgvTms.OpcUaAdapter.Tests/
│       └── OpcUaServiceTests.cs
└── README.md
```

## 核心依赖 (.csproj)

```xml
<Project Sdk="Microsoft.NET.Sdk.Web">

  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <RootNamespace>AgvTms.OpcUaAdapter</RootNamespace>
  </PropertyGroup>

  <ItemGroup>
    <!-- ASP.NET Core -->
    <PackageReference Include="Microsoft.AspNetCore.OpenApi" Version="8.0.0" />
    
    <!-- OPC UA .NET Standard (官方) -->
    <PackageReference Include="OPCFoundation.NetStandard.Opc.Ua" Version="1.5.374.111" />
    <PackageReference Include="OPCFoundation.NetStandard.Opc.Ua.Client" Version="1.5.374.111" />
    
    <!-- Kafka (Confluent) -->
    <PackageReference Include="Confluent.Kafka" Version="2.3.0" />
    
    <!-- Protocol Buffers -->
    <PackageReference Include="Google.Protobuf" Version="3.25.1" />
    
    <!-- Health Checks & Metrics -->
    <PackageReference Include="AspNetCore.HealthChecks.Publisher.Prometheus" Version="8.0.0" />
    
    <!-- Serilog (日志) -->
    <PackageReference Include="Serilog.AspNetCore" Version="8.0.0" />
    <PackageReference Include="Serilog.Sinks.Console" Version="5.0.1" />
    
    <!-- Testing -->
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.9.0" />
    <PackageReference Include="xunit" Version="2.7.0" />
    <PackageReference Include="Moq" Version="4.20.70" />
  </ItemGroup>

</Project>
```

## 核心代码示例

### 1. Program.cs (入口与依赖注入)

```csharp
// Program.cs — Minimal API with full DI configuration

using AgvTms.OpcUaAdapter.Services;
using AgvTms.OpcUaAdapter.Kafka;
using AgvTms.OpcUaAdapter.Middleware;
using Serilog;

Log.Logger = new LoggerConfiguration()
    .WriteTo.Console()
    .CreateLogger();

var builder = WebApplication.CreateBuilder(args);

// Use Serilog
builder.Host.UseSerilog();

// ==================== Services ====================

// OPC UA Service (Singleton for persistent sessions)
builder.Services.AddSingleton<IOpcUaService, OpcUaService>();
builder.Services.AddSingleton<IProtocolAdapterManager, ProtocolAdapterManager>();

// Kafka Producer/Consumer
builder.Services.AddSingleton<IKafkaProducer, KafkaEventProducer>();
builder.Services.AddHostedService<KafkaBackgroundConsumer>();

// Configure Options
builder.Services.Configure<OpcUaConfig>(
    builder.Configuration.GetSection("OpcUa"));

// HTTP Client (for calling Python backend)
builder.Services.AddHttpClient<IPythonBackendClient, PythonBackendClient>();

// CORS (允许前端调用)
builder.Services.AddCors(options =>
{
    options.AddDefaultPolicy(policy =>
    {
        policy.WithOrigins("http://localhost:3000")
              .AllowAnyHeader()
              .AllowAnyMethod();
    });
});

// Health Checks
builder.Services.AddHealthChecks()
    .AddCheck<OpcUaHealthCheck>("opcua")
    .AddKafka(builder.Configuration.GetSection("Kafka:BootstrapServers")?.Value ?? "kafka:9092", "kafka");

// OpenAPI/Swagger
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen(c =>
{
    c.SwaggerDoc("v1", new Microsoft.OpenApi.Models.OpenApiInfo 
    { 
        Title = "AGV-TMS OPC-UA Adapter API", 
        Version = "v1",
        Description = ".NET-based industrial protocol adapter service"
    });
});

var app = builder.Build();

// ==================== Middleware Pipeline ====================

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseCors();
app.UseMiddleware<RequestLoggingMiddleware>();
app.MapHealthChecks("/healthz");

// ==================== API Endpoints ====================

var opcUaGroup = app.MapGroup("/api/v2/opcua")
    .WithName("OpcUa")
    .WithOpenApi();

// 连接管理
opcUaGroup.MapPost("/connect", async (IOpcUaService opcUa, ConnectRequest req) =>
{
    var result = await opcUa.ConnectAsync(req.EndpointUrl, req.SecurityPolicy);
    return Results.Ok(result);
})
.WithName("Connect");

opcUaGroup.MapPost("/disconnect", async (IOpcUaService opcUa) =>
{
    await opcUa.DisconnectAsync();
    return Results.Ok(new { status = "disconnected" });
})
.WithName("Disconnect");

// 节点浏览
opcUaGroup.MapGet("/nodes/{nodeId}", async (
    string nodeId, IOpcUaService opcUa) =>
{
    var node = await opcUa.BrowseNodeAsync(nodeId);
    return node is not null ? Results.Ok(node) : Results.NotFound();
})
.WithName("BrowseNode");

opcUaGroup.MapGet("/nodes/{nodeId}/value", async (
    string nodeId, IOpcUaService opcUa) =>
{
    var value = await opcUa.ReadValueAsync(nodeId);
    return value is not null ? Results.Ok(value) : Results.NotFound();
})
.WithName("ReadValue");

opcUaGroup.MapPost("/nodes/{nodeId}/value", async (
    string nodeId, WriteValueRequest req, IOpcUaService opcUa) =>
{
    await opcUa.WriteValueAsync(nodeId, req.Value, req.DataType);
    return Results.Ok(new { success = true });
})
.WithName("WriteValue");

// 订阅管理
opcUaGroup.MapPost("/subscriptions", async (
    CreateSubscriptionRequest req, IOpcUaService opcUa, IKafkaProducer kafka) =>
{
    var subscriptionId = await opcUa.SubscribeAsync(
        req.NodeIds, 
        intervalMs: req.IntervalMs,
        onChange: (data) => 
        {
            // 自动转发到 Kafka → Python 后端
            kafka.PublishOpcUaDataChange(data);
        }
    );
    return Results.Created($"/api/v2/opcua/subscriptions/{subscriptionId}", 
        new { subscription_id = subscriptionId });
})
.WithName("CreateSubscription");

opcUaGroup.MapDelete("/subscriptions/{subscriptionId}", async (
    string subscriptionId, IOpcUaService opcUa) =>
{
    await opcUa.UnsubscribeAsync(subscriptionId);
    return Results.NoContent();
})
.WithName("DeleteSubscription");

// 网关状态端点
app.MapGatewayStatus("/gateway/status");

app.Run();
```

### 2. OPC UA 核心服务实现

```csharp
// Services/OpcUaService.cs

using Opc.Ua;
using Opc.Ua.Client;
using System.Collections.Concurrent;

namespace AgvTms.OpcUaAdapter.Services;

/// <summary>
/// OPC UA 客户端服务.
/// 对应 Python backend/app/adapters/opcua_adapter.py 的功能
/// </summary>
public class OpcUaService : IOpcUaService, IDisposable
{
    private readonly ILogger<OpcUaService> _logger;
    private readonly OpcUaConfig _config;
    private Session? _session;
    private readonly ConcurrentDictionary<string, Subscription> _subscriptions = new();
    private bool _disposed = false;

    public OpcUaService(ILogger<OpcUaService> logger, IOptions<OpcUaConfig> config)
    {
        _logger = logger;
        _config = config.Value;
        
        // Suppress certificate validation in dev mode
        if (_config.SkipCertificateValidation)
        {
            ApplicationInstance.ApplicationCertificate = null;
        }
    }

    /// <summary>
    /// Connect to OPC UA Server.
    /// </summary>
    public async Task<ConnectResult> ConnectAsync(string endpointUrl, string? securityPolicy = null)
    {
        try
        {
            // Validate endpoint
            var selectedEndpoint = await SelectEndpointAsync(endpointUrl);
            if (selectedEndpoint == null)
            {
                throw new Exception($"No suitable endpoint found at {endpointUrl}");
            }

            // Configure security
            var endpointConfiguration = EndpointConfiguration.Create(_config.ApplicationUri);
            var endpoint = new ConfiguredEndpoint(
                null, selectedEndpoint, endpointConfiguration);

            // Create session
            var userIdentity = new UserIdentity(
                _config.Username ?? "", 
                _config.Password ?? "");
            
            _session = Session.Create(
                application: new ApplicationConfiguration
                {
                    ApplicationName = _config.ApplicationName,
                    ApplicationType = ApplicationType.Client,
                    ApplicationUri = _config.ApplicationUri,
                    SecurityConfiguration = new SecurityConfiguration
                    {
                        ApplicationCertificate = CertificateFactory.Create(),
                        TrustedPeerCertificates = new CertificateTrustList(),
                        RejectedCertificateStore = new CertificateTrustList()
                    },
                    TransportQuotas = new TransportQuotas
                    {
                        OperationTimeout = _config.OperationTimeout,
                        MaxStringLength = int.MaxValue,
                        MaxByteStringLength = int.MaxValue,
                        MaxArrayLength = 65535,
                        MaxMessageSize = int.MaxValue,
                        MaxBufferSize = int.MaxValue,
                        ChannelLifetime = 600000,
                        SecurityTokenLifetime = 600000,
                    },
                },
                config: null,
                updatedConfig: null,
                endpoint: endpoint,
                updateBeforeConnect: false,
                checkDomain: false,
                sessionName: $"AGV-TMS-Adapter-{Guid.NewGuid():N}",
                sessionTimeout: _config.SessionTimeout,
                identity: userIdentity,
                preferredLocales: null
            );

            // Activate session and create default subscriptions
            _session.KeepAlive += OnKeepAlive;
            
            // Auto-create default subscription for monitoring
            if (_config.AutoSubscribe)
            {
                await CreateDefaultSubscriptionAsync();
            }

            _logger.LogInformation(
                "Connected to OPC UA server: {Endpoint} [Session={SessionId}]",
                endpointUrl, _session.AuthenticatedToken);

            return new ConnectResult(
                SessionId: _session.SessionId.ToString(),
                EndpointUrl: endpointUrl,
                ConnectedAt: DateTime.UtcNow
            );
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to connect to OPC UA server: {Endpoint}", endpointUrl);
            throw;
        }
    }

    /// <summary>
    /// Browse node and its children.
    /// </summary>
    public async Task<OpcUaNode?> BrowseNodeAsync(string nodeIdStr)
    {
        EnsureConnected();

        var nodeId = NodeId.Parse(nodeIdStr);
        var description = new ViewDescription
        {
            Timestamp = DateTime.MinValue,
            ViewId = NodeId.Null
        };

        var references = await _session.BrowseAsync(
            null, null, description, 1000,
            BrowseDirection.Forward, ReferenceTypeIds.HierarchicalReferences,
            true, (uint)NodeClass.Variable | (uint)NodeClass.Object | (uint)NodeClass.Method,
            new Collection<NodeId> { nodeId });

        var node = new OpcUaNode
        {
            NodeId = nodeIdStr,
            DisplayName = await GetDisplayName(nodeId),
            NodeClass = await GetNodeClass(nodeId),
            Children = new List<OpcUaChild>()
        };

        if (references.Results.Count > 0 && referenceResults[0].References != null)
        {
            foreach (var ref in referenceResults[0].References)
            {
                node.Children.Add(new OpcUaChild
                {
                    NodeId = ref.NodeId.ToString(),
                    DisplayName = ref.DisplayName?.Text ?? "",
                    TypeDefinition = ref.TypeDefinition?.ToString() ?? "",
                    NodeClass = ref.NodeClass.ToString()
                });
            }
        }

        return node;
    }

    /// <summary>
    /// Read current value of a node.
    /// </summary>
    public async Task<DataValue?> ReadValueAsync(string nodeIdStr)
    {
        EnsureConnected();

        var nodesToRead = new ReadValueIdCollection
        {
            new ReadValueId { NodeId = NodeId.Parse(nodeIdStr), AttributeId = Attributes.Value }
        };

        var response = await _session.ReadAsync(
            null, 0, TimestampsToReturn.Both, nodesToRead);

        if (response.Results.Count > 0 && !StatusCode.IsBad(response.Results[0].StatusCode))
        {
            return response.Results[0];
        }

        _logger.LogWarning("Failed to read node {NodeId}: {StatusCode}",
            nodeIdStr, response.Results[0]?.StatusCode);
        return null;
    }

    /// <summary>
    /// Write value to a node.
    /// </summary>
    public async Task WriteValueAsync(string nodeIdStr, object value, string dataType)
    {
        EnsureConnected();

        var typeId = GetBuiltInTypeId(dataType);
        var variant = new Variant(value, typeId);

        var nodesToWrite = new WriteValueCollection
        {
            new WriteValue
            {
                NodeId = NodeId.Parse(nodeIdStr),
                AttributeId = Attributes.Value,
                Value = new DataValue(variant)
            }
        };

        var response = await _session.WriteAsync(null, nodesToWrite);
        
        if (response.Results.Count > 0 && StatusCode.IsBad(response.Results[0]))
        {
            throw new Exception($"Write failed: {response.Results[0]}");
        }
    }

    /// <summary>
    /// Subscribe to data changes on multiple nodes.
    /// </summary>
    public async Task<string> SubscribeAsync(
        List<string> nodeIds, 
        int intervalMs = 1000,
        Action<OpcUaDataChange>? onChange = null)
    {
        EnsureConnected();

        var subscription = new Subscription(_session.DefaultSubscription)
        {
            PublishingEnabled = true,
            PublishingInterval = intervalMs,
            LifetimeCount = 100,
            KeepAliveCount = 10,
            MaxNotificationsPerPublish = 10000,
            Priority = 100,
            DisplayName = $"AGVTMS-Sub-{Guid.NewGuid():N()[..8]}"
        };
        
        _session.AddSubscription(subscription);
        subscription.Create();

        var monitoredItems = new MonitoredItemCollection();
        foreach (var nodeId in nodeIds)
        {
            var item = new MonitoredItem(subscription.DefaultItem)
            {
                DisplayName = nodeId,
                StartNodeId = NodeId.Parse(nodeId),
                AttributeId = Attributes.Value,
                SamplingInterval = intervalMs,
                QueueSize = 10,
                DiscardOldest = true,
                MonitoringMode = MonitoringMode.Reporting
            };

            item.Notification += (monItem, e) =>
            {
                if (e.NotificationValue is MonitoredItemNotification notification)
                {
                    var dataChange = new OpcUaDataChange
                    {
                        NodeId = nodeId,
                        Value = notification.NotificationValue.WrappedValue.Value,
                        SourceTimestamp = notification.NotificationValue.SourceTimestamp,
                        ServerTimestamp = notification.NotificationValue.ServerTimestamp,
                        StatusCode = notification.NotificationValue.StatusCode.ToString()
                    };

                    onChange?.Invoke(dataChange);
                    
                    // Also publish via Kafka if configured
                    _onDataChangeHandlers.TryGetValue(subscription.Id, out var handler);
                    handler?.Invoke(dataChange);
                }
            };

            monitoredItems.Add(item);
        }

        subscription.AddItems(monitoredItems);
        subscription.ApplyChanges();

        _subscriptions[subscription.Id.ToString()] = subscription;
        _onDataChangeHandlers[subscription.Id] = onChange;

        _logger.LogInformation("Created subscription {SubId} for {Count} nodes [interval={Interval}ms]",
            subscription.Id, nodeIds.Count, intervalMs);

        return subscription.Id.ToString();
    }

    /// <summary>
    /// Disconnect from OPC UA server.
    /// </summary>
    public async Task DisconnectAsync()
    {
        if (_session != null && !_session.Disposed)
        {
            foreach (var sub in _subscriptions.Values)
            {
                try { sub.Delete(); } catch { /* ignore */ }
            }
            _subscriptions.Clear();
            
            _session.Close(10000);
            _session.Dispose();
            _session = null;
            
            _logger.LogInformation("Disconnected from OPC UA server");
        }
    }

    // ... Private helper methods ...

    private void EnsureConnected()
    {
        if (_session == null || _session.Disposed)
        {
            throw new InvalidOperationException("Not connected to OPC UA server. Call ConnectAsync first.");
        }
    }

    private void OnKeepAlive(Session session, KeepAliveEventArgs e)
    {
        if (StatusCode.IsBad(e.Status))
        {
            _logger.LogWarning("OPC UA keep-alive failed: {Status}, reconnecting...", e.Status);
            // Trigger reconnection logic
        }
    }

    public void Dispose()
    {
        if (!_disposed)
        {
            DisconnectAsync().GetAwaiter().GetResult();
            _disposed = true;
        }
    }
}
```

### 3. Kafka 事件桥接器 (.NET)

```csharp
// Kafka/KafkaEventProducer.cs

using Confluent.Kafka;
using System.Text.Json;

namespace AgvTms.OpcUaAdapter.Kafka;

/// <summary>
/// Kafka Event Producer — 发布 OPC UA 数据变更到 Kafka.
/// 格式兼容 Python 后端的 CloudEvents 1.0 标准.
/// </summary>
public class KafkaEventProducer : IKafkaProducer, IDisposable
{
    private readonly ILogger<KafkaEventProducer> _logger;
    private readonly IProducer<string, string> _producer;
    private readonly KafkaConfig _config;
    private bool _disposed = false;

    public KafkaEventProducer(ILogger<KafkaEventProducer> logger, IOptions<KafkaConfig> config)
    {
        _logger = logger;
        _config = config.Value;

        var producerConfig = new ProducerConfig
        {
            BootstrapServers = _config.BootstrapServers,
            ClientId = "agvtms-opcua-dotnet",
            Acks = Acks.All,
            LingerMs = 5,
            CompressionType = CompressionType.Gzip,
            EnableIdempotence = true,
            // Retry config
            MessageSendMaxRetries = 3,
            RetryBackoffMs = 100,
        };

        _producer = new ProducerBuilder<string, string>(producerConfig)
            .SetErrorHandler((producer, error) => 
                _logger.LogError("Kafka producer error: {Reason} ({Code})", error.Reason, error.Code))
            .Build();
    }

    /// <summary>
    /// Publish OPC UA data change event to Kafka.
    /// Topic: agvtms.protocol.data_changed.v1
    /// Format: CloudEvents 1.0
    /// </summary>
    public async Task PublishOpcUaDataChange(OpcUaDataChange data)
    {
        var cloudEvent = new
        {
            specversion = "1.0",
            type = "agvtms.protocol.data_changed.v1",
            source = "/agvtms/dotnet/opcua",
            id = Guid.NewGuid().ToString(),
            time = DateTime.UtcNow.ToString("o"),
            datacontenttype = "application/json",
            subject = data.NodeId,
            data = new
            {
                protocol = "opc-ua",
                node_id = data.NodeId,
                value = data.Value,
                value_type = data.Value?.GetType()?.Name ?? "unknown",
                source_timestamp = data.SourceTimestamp,
                server_timestamp = data.ServerTimestamp,
                status_code = data.StatusCode
            }
        };

        var message = new Message<string, string>
        {
            Key = data.NodeId,
            Value = JsonSerializer.Serialize(cloudEvent, new JsonSerializerOptions { WriteIndented = false })
        };

        try
        {
            var deliveryResult = await _producer.ProduceAsync(
                _config.TopicPrefix + ".protocol.data_changed.v1", message);
                
            _logger.LogDebug(
                "Published OPC UA data change: topic={Topic}, partition={Partition}, offset={Offset}",
                deliveryResult.Topic, deliveryResult.Partition, deliveryResult.Offset);
        }
        catch (ProduceException<string, string> ex)
        {
            _logger.LogError(ex, "Failed to publish OPC UA data change for node {NodeId}", data.NodeId);
            throw;
        }
    }

    /// <summary>
    /// Publish generic event to any topic.
    /// </summary>
    public async Task PublishAsync(string topic, object eventData, string? key = null)
    {
        var cloudEvent = new Dictionary<string, object?>
        {
            ["specversion"] = "1.0",
            ["type"] = topic,
            ["source"] = "/agvtms/dotnet",
            ["id"] = Guid.NewGuid().ToString(),
            ["time"] = DateTime.UtcNow.ToString("o"),
            ["datacontenttype"] = "application/json",
            ["data"] = eventData
        };

        var message = new Message<string, string>
        {
            Key = key,
            Value = JsonSerializer.Serialize(cloudEvent)
        };

        await _producer.ProduceAsync(topic, message);
    }

    public void Dispose()
    {
        if (!_disposed)
        {
            _producer.Flush(TimeSpan.FromSeconds(10));
            _producer.Dispose();
            _disposed = true;
        }
    }
}
```

### 4. 配置文件 (appsettings.json)

```json
{
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "AgvTms": "Debug"
    }
  },

  "OpcUa": {
    "ApplicationName": "AGV-TMS-OPC-UA-Adapter",
    "ApplicationUri": "urn:agvtms:opcua-adapter",
    "OperationTimeout": 15000,
    "SessionTimeout": 600000,
    "AutoSubscribe": true,
    "SkipCertificateValidation": true,
    "Username": "",
    "Password": ""
  },

  "Kafka": {
    "BootstrapServers": "kafka:9092",
    "TopicPrefix": "agvtms",
    "ConsumerGroup": "opcua-adapter-service",
    "AutoCommit": false,
    "AutoOffsetReset": "Latest"
  },

  "PythonBackend": {
    "BaseUrl": "http://agvtms-backend:8000",
    "TimeoutSeconds": 30
  },

  "AllowedHosts": "*"
}
```

## 与 Python 后端的集成架构

```
┌─────────────────────┐    OPC-UA协议     ┌──────────────────┐
│  PLC / 工业设备      │ ◄═════════════►  │  .NET OPC-UA      │
│                     │                   │  Adapter Service  │
└─────────────────────┘                   └────────┬─────────┘
                                                  │ Kafka
                                                  ▼
┌─────────────────────┐    HTTP/WS          ┌──────────────────┐
│  React Frontend     │ ◄══════════════►  │  Python FastAPI   │
│  (数字孪生可视化)    │                   │  Backend Gateway  │
└─────────────────────┘                   └──────────────────┘
```

**数据流向**:
1. **PLC设备状态变更** → .NET OPC-UA订阅捕获 → **Kafka发布**
2. **Python FastAPI** 消费Kafka事件 → **更新业务状态** → **WebSocket推送给前端**
3. **前端控制指令** → Python FastAPI → **Kafka发布控制命令** → .NET消费并写入PLC

## 部署配置 (Docker Compose)

在主 docker-compose.yml 中添加:

```yaml
  # .NET OPC-UA Adapter Service
  agvtms-opcua-dotnet:
    build:
      context: ./backend/services/dotnet-opcua-example/src/AgvTms.OpcUaAdapter
      dockerfile: Dockerfile
    container_name: agvtms-opcua-dotnet
    ports:
      - "5000:5000"
    environment:
      - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
      - PYTHON_BACKEND_URL=http://backend:8000
      - OPCUA__SkipCertificateValidation=true
    depends_on:
      - kafka
      - backend
    networks:
      - agvtms-network
    restart: unless-stopped
```
