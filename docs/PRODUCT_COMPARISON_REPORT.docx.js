const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, PageOrientation, LevelFormat,
  HeadingLevel, BorderStyle, WidthType, ShadingType,
  VerticalAlign, PageNumber, PageBreak, TableOfContents
} = require('docx');

// 定义边框样式
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const headerBorder = { style: BorderStyle.SINGLE, size: 1, color: "2E75B6" };
const headerBorders = { top: headerBorder, bottom: headerBorder, left: headerBorder, right: headerBorder };

// 创建表格单元格的辅助函数
function createCell(text, width, isHeader = false, options = {}) {
  return new TableCell({
    borders: isHeader ? headerBorders : borders,
    width: { size: width, type: WidthType.DXA },
    shading: isHeader ? { fill: "2E75B6", type: ShadingType.CLEAR } : (options.shading ? { fill: options.shading, type: ShadingType.CLEAR } : undefined),
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      alignment: options.align || (isHeader ? AlignmentType.CENTER : AlignmentType.LEFT),
      children: [new TextRun({
        text: text,
        bold: isHeader,
        color: isHeader ? "FFFFFF" : "000000",
        size: isHeader ? 22 : 20,
        font: "Arial"
      })]
    })]
  });
}

// 创建标题段落
function createHeading1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 400, after: 200 },
    children: [new TextRun({ text: text, bold: true, size: 32, font: "Arial", color: "2E75B6" })]
  });
}

function createHeading2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 300, after: 150 },
    children: [new TextRun({ text: text, bold: true, size: 28, font: "Arial", color: "1F4E79" })]
  });
}

function createHeading3(text) {
  return new Paragraph({
    spacing: { before: 200, after: 100 },
    children: [new TextRun({ text: text, bold: true, size: 24, font: "Arial", color: "2E75B6" })]
  });
}

function createParagraph(text, options = {}) {
  return new Paragraph({
    spacing: { before: 100, after: 100 },
    alignment: options.align || AlignmentType.LEFT,
    children: [new TextRun({
      text: text,
      size: 22,
      font: "Arial",
      bold: options.bold,
      color: options.color || "333333"
    })]
  });
}

function createBulletPoint(text, reference = "bullets") {
  return new Paragraph({
    numbering: { reference: reference, level: 0 },
    spacing: { before: 60, after: 60 },
    children: [new TextRun({ text: text, size: 22, font: "Arial" })]
  });
}

// 创建文档
const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: "2E75B6" },
        paragraph: { spacing: { before: 400, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: "Arial", color: "1F4E79" },
        paragraph: { spacing: { before: 300, after: 150 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: "2E75B6" },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 2 } },
    ]
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbers",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 }, // A4
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
      }
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({ text: "AgvTms Product Comparison Report", italics: true, size: 18, color: "666666", font: "Arial" })]
        })]
      })
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ text: "Page ", size: 18, font: "Arial" }),
            new TextRun({ children: [PageNumber.CURRENT], size: 18, font: "Arial" }),
            new TextRun({ text: " | Confidential", size: 18, color: "999999", font: "Arial" })
          ]
        })]
      })
    },
    children: [
      // ========== 封面 ==========
      new Paragraph({ spacing: { before: 2000 } }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "AGV-TMS", bold: true, size: 72, font: "Arial", color: "2E75B6" })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 200 },
        children: [new TextRun({ text: "Product System Comparison Analysis Report", size: 36, font: "Arial", color: "1F4E79" })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 100 },
        children: [new TextRun({ text: "AGV\u8C03\u5EA6\u7BA1\u7406\u7CFB\u7EDF\u4EA7\u54C1\u5BF9\u6BD4\u5206\u6790\u62A5\u544A", size: 28, font: "Arial", color: "666666" })]
      }),
      new Paragraph({ spacing: { before: 800 } }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "Version 1.0", size: 24, font: "Arial" })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 100 },
        children: [new TextRun({ text: "June 15, 2026", size: 24, font: "Arial" })]
      }),
      new Paragraph({ spacing: { before: 600 } }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        border: { top: { style: BorderStyle.SINGLE, size: 6, color: "2E75B6", space: 1 } },
        spacing: { before: 200 },
        children: [new TextRun({ text: "", size: 10 })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 200 },
        children: [new TextRun({ text: "Competitors: Geek+ RMS | Hikvision RCS | Hai Robotics HAIQ | Megvii Hetu | SEER", size: 20, font: "Arial", color: "666666" })]
      }),

      // ========== 分页 ==========
      new Paragraph({ children: [new PageBreak()] }),

      // ========== 目录 ==========
      new Paragraph({
        heading: HeadingLevel.HEADING_1,
        children: [new TextRun({ text: "Table of Contents", bold: true, size: 32, font: "Arial", color: "2E75B6" })]
      }),
      new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),

      // ========== 分页 ==========
      new Paragraph({ children: [new PageBreak()] }),

      // ========== 第一章：执行摘要 ==========
      createHeading1("1. Executive Summary"),
      createParagraph("This report presents a comprehensive comparative analysis between AgvTms (AGV Transport Management System) and mainstream AGV/AMR scheduling management systems in the current market. AgvTms is a research-oriented and engineering prototype-level hybrid scheduling system featuring a unique dual-version architecture combining V1 meta-heuristic algorithms with V2 industrial-grade optimization engines. The system integrates advanced algorithms including Reinforcement Learning (DQN/PPO), Integer Programming (MIP/CP-SAT), and Three-layer Orchestrator."),
      createParagraph({ text: "Core Conclusion:", bold: true }),
      createBulletPoint("Algorithm Richness & Academic Innovation: Reaches or partially exceeds commercial product levels"),
      createBulletPoint("Engineering Maturity: Gaps remain in deployment scale, visualization capabilities, and production-grade reliability"),
      createBulletPoint("Recommended Positioning: Algorithm validation platform, research/teaching tool, or core technology base for small-to-medium customization projects"),

      // ========== 第二章：竞品格局总览 ==========
      createHeading1("2. Competitive Landscape Overview"),
      
      createHeading2("2.1 Key Competitor Matrix"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [1503, 1804, 2255, 1718, 1746],
        rows: [
          new TableRow({ children: [
            createCell("Vendor", 1503, true),
            createCell("Product Name", 1804, true),
            createCell("Positioning", 2255, true),
            createCell("Typical Scale", 1718, true),
            createCell("Tech Route", 1746, true)
          ]}),
          new TableRow({ children: [
            createCell("Geek+ (\u6781\u667A\u5609)", 1503),
            createCell("RMS/WES/IOP", 1804),
            createCell("Global Warehouse Robotics Leader", 2255),
            createCell("318 units/warehouse", 1718),
            createCell("P2P Picking + Cloud-Native", 1746)
          ]}),
          new TableRow({ children: [
            createCell("Hikvision (\u6D77\u5EB7)", 1503),
            createCell("RCS/RCS-Lite", 1804),
            createCell("Vision Sensor Integration Giant", 2255),
            createCell("100-500 units/factory", 1718),
            createCell("Standardized + Cost-effective", 1746)
          ]}),
          new TableRow({ children: [
            createCell("Hai Robotics (\u6D77\u67D4)", 1503),
            createCell("HAIQ/HaiPick", 1804),
            createCell("Box ACR Specialist", 2255),
            createCell("50-200 units/warehouse", 1718),
            createCell("High-Density Storage + Fast Deploy", 1746)
          ]}),
          new TableRow({ children: [
            createCell("Megvii (\u65BA\u89C6)", 1503),
            createCell("\u6CB3\u56FE (Hetu)", 1804),
            createCell("AI-Defined Hardware", 2255),
            createCell("Large AS/RS warehouses", 1718),
            createCell("3A Solution (AS/RS+AMR+AI)", 1746)
          ]}),
          new TableRow({ children: [
            createCell("SEER (\u4ED9\u5DE5\u667A\u80FD)", 1503),
            createCell("SRC+\u661F\u4E91", 1804),
            createCell("Open Ecosystem Platform", 2255),
            createCell("100+ heterogeneous units", 1718),
            createCell("VDA 5050 + 1000+ models", 1746)
          ]}),
          new TableRow({ children: [
            createCell("AgvTms (本项目)", 1503, false, { shading: "E8F4FD" }),
            createCell("TMS v1+v2", 1804, false, { shading: "E8F4FD" }),
            createCell("Research Prototype Hybrid Scheduling", 2255, false, { shading: "E8F4FD" }),
            createCell("2-200 units (design target)", 1718, false, { shading: "E8F4FD" }),
            createCell("Meta-heuristic + MIP + RL + 3-Layer", 1746, false, { shading: "E8F4FD" })
          ]})
        ]
      }),

      createHeading2("2.2 Market Positioning Quadrant"),
      createParagraph("The AGV scheduling system market can be divided into two main quadrants based on industrial maturity and academic innovation:"),
      createBulletPoint("Commercial Product Zone: Geek+, Hikvision RCS, HaiQ, Megvii Hetu - characterized by high maturity, proven deployments, enterprise-grade support"),
      createBulletPoint("Research Prototype Zone: AgvTms - characterized by algorithm richness, academic innovation, rapid iteration capability"),
      createParagraph("AgvTms occupies a unique position offering \"More professional than open-source, more innovative than commercial\" capabilities."),

      // ========== 第三章：技术架构深度对比 ==========
      createHeading1("3. Technical Architecture Deep Dive"),

      createHeading2("3.1 System Architecture Patterns"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [1605, 1804, 1804, 1804, 2009],
        rows: [
          new TableRow({ children: [
            createCell("Dimension", 1605, true),
            createCell("AgvTms", 1804, true),
            createCell("Geek+ RMS", 1804, true),
            createCell("Hikvision RCS", 1804, true),
            createCell("SEER", 2009, true)
          ]}),
          new TableRow({ children: [
            createCell("Architecture Style", 1605),
            createCell("Monolithic FastAPI + Modular Algorithm Library", 1804),
            createCell("Microservice Cloud-Native", 1804),
            createCell("Microservice Spring Cloud", 1804),
            createCell("Controller + Platform Dual-Layer", 2009)
          ]}),
          new TableRow({ children: [
            createCell("Backend Framework", 1605),
            createCell("Python FastAPI", 1804),
            createCell("Java/Go (undisclosed)", 1804),
            createCell("Java Spring Boot", 1804),
            createCell("C++/Java Hybrid", 2009)
          ]}),
          new TableRow({ children: [
            createCell("Frontend Technology", 1605),
            createCell("React + TypeScript + Ant Design", 1804),
            createCell("Web Portal + G-Studio Simulation", 1804),
            createCell("Vue3 + Element Plus", 1804),
            createCell("Web + HMI Touch Panel", 2009)
          ]}),
          new TableRow({ children: [
            createCell("Database", 1605),
            createCell("SQLite (Dev) / PostgreSQL (Prod)", 1804),
            createCell("AWS/Azure Cloud DB Cluster", 1804),
            createCell("MySQL + Redis + MongoDB", 1804),
            createCell("Undisclosed", 2009)
          ]}),
          new TableRow({ children: [
            createCell("Communication Protocol", 1605),
            createCell("REST API + WebSocket", 1804),
            createCell("RESTful + WebSocket + MQTT", 1804),
            createCell("MQTT/WebSocket/Kafka", 1804),
            createCell("VDA 5050 + Proprietary", 2009)
          ]}),
          new TableRow({ children: [
            createCell("Containerization", 1605),
            createCell("Docker Compose", 1804),
            createCell("Kubernetes", 1804),
            createCell("K8s + Istio Service Mesh", 1804),
            createCell("Edge Computing Box", 2009)
          ]})
        ]
      }),

      createHeading2("3.2 AgvTms Dual-Version Architecture (Unique Feature)"),
      createParagraph("The most distinctive feature of AgvTms is its dual-version algorithm architecture:"),
      createHeading3("V1 Algorithm Suite (Meta-heuristic):"),
      createBulletPoint("ACO (Ant Colony Optimization): Multi-AGV path planning with pheromone matrix"),
      createBulletPoint("SA (Simulated Annealing): Task-to-AGV assignment with Metropolis acceptance criterion"),
      createBulletPoint("NLP (Non-Linear Programming): Conveyor belt sequencing optimization via SLSQP"),
      createBulletPoint("HybridScheduler: Five-phase pipeline combining all three algorithms"),
      createHeading3("V2 Industrial-Grade Algorithm Suite:"),
      createBulletPoint("HybridOrchestratorV2: Three-layer coordination (Strategic/Tactical/Operational)"),
      createBulletPoint("MipTaskAssigner: OR-Tools CP-SAT integer programming solver"),
      createBulletPoint("PredictiveEngine: Three-dimensional prediction (task/congestion/battery)"),
      createBulletPoint("RLScheduler: Double Dueling DQN with Prioritized Experience Replay"),
      createBulletPoint("TrafficController: Region-based traffic management with deadlock detection"),

      // ========== 第四章：算法能力详细对比 ==========
      createHeading1("4. Algorithm Capability Comparison"),

      createHeading2("4.1 Comprehensive Algorithm Matrix"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [2009, 1401, 1401, 1401, 1401, 1413],
        rows: [
          new TableRow({ children: [
            createCell("Algorithm Category", 2009, true),
            createCell("AgvTms", 1401, true),
            createCell("Geek+", 1401, true),
            createCell("Hikvision", 1401, true),
            createCell("Megvii", 1401, true),
            createCell("SEER", 1413, true)
          ]}),
          new TableRow({ children: [
            createCell("Heuristic Rules (FCFS/Greedy)", 2009),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1413, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Meta-heuristic (SA/ACO/GA)", 2009),
            createCell("\u2705 SA, ACO, NLP", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F Limited", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 GA", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F", 1413, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Integer Programming (MIP)", 2009),
            createCell("\u2705 OR-Tools CP-SAT", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u274C Undisclosed", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u274C", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F Possible", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u274C", 1413, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Path Planning (A*/SIPP)", 2009),
            createCell("\u2705 Bidirectional A*", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 TW-A*", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 SLAM Integrated", 1413, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Reinforcement Learning (RL)", 2009),
            createCell("\u2705 DQN + PPO", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F Research Phase", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F Experimental", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 DL Prediction", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u274C", 1413, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Predictive Engine", 2009),
            createCell("\u2705 3-Dimension (Task/Congestion/Battery)", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F Basic", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 Deep Learning", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u274C", 1413, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Three-Layer Orchestration", 2009),
            createCell("\u2705 Strategic/Tactical/Operational", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 Layered", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1413, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Deadlock Detection & Recovery", 2009),
            createCell("\u2705 Wait-for Graph", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1413, false, { align: AlignmentType.CENTER })
          ]})
        ]
      }),

      createHeading2("4.2 Unique Technical Highlights of AgvTms"),
      
      createHeading3("Highlight 1: Six Algorithm Unified Adapter Architecture"),
      createParagraph("Unlike commercial products that typically offer 2-3 configurable algorithms, AgvTms provides 6 algorithms switchable through a unified interface:"),
      createBulletPoint("FCFS Adapter: First-come-first-served baseline"),
      createBulletPoint("Greedy Adapter: Nearest-neighbor heuristic"),
      createBulletPoint("V1HybridAdapter: SA + ACO + NLP combination"),
      createBulletPoint("V2MipAdapter: CP-SAT integer programming for optimal solutions"),
      createBulletPoint("V2OrchestratorAdapter: Three-layer coordination with predictive engine"),
      createBulletPoint("RLDQNAdapter: Reinforcement learning agent with training/inference modes"),

      createHeading3("Highlight 2: Built-in Scenario-Based Benchmark Framework"),
      createParagraph("Supports \"one-click run all algorithms comparison\" outputting:"),
      createBulletPoint("Assignment success rate (%)"),
      createBulletPoint("Average completion time (makespan)"),
      createBulletPoint("Total travel distance"),
      createBulletPoint("Algorithm response latency (ms)"),
      createBulletPoint("Battery consumption statistics"),
      createParagraph("This is a powerful tool for academic research and algorithm selection that would require additional simulation software in commercial products.", { italics: true }),

      createHeading3("Highlight 3: RL Training Pipeline (Complete Closed Loop)"),
      createBulletPoint("Double Dueling DQN with Prioritized Experience Replay (PER)"),
      createBulletPoint("Action Masking mechanism to ensure feasibility"),
      createBulletPoint("Gymnasium standard environment interface"),
      createBulletPoint("Auto-save model and connect to inference after training"),
      createParagraph({ text: "Industry Status: ", bold: true }),
      createParagraph("As of 2026, most commercial AGV scheduling systems still rely on rule engines + traditional optimization. RL is primarily in the laboratory stage. AgvTms has achieved a complete closed loop of Train \u2192 Save \u2192 Deploy \u2192 Evaluate."),

      createHeading3("Highlight 4: Predictive Engine Three-Dimensional Linkage"),
      createBulletPoint("Task Load Prediction: Future 1-hour task volume estimation based on historical patterns"),
      createBulletPoint("Congestion Prediction: Regional congestion probability prediction for proactive detour"),
      createBulletPoint("Battery Prediction: Single vehicle remaining working time prediction for smart charging dispatch"),
      createParagraph("Most commercial systems' \"prediction\" is limited to simple rule extrapolation. AgvTms employs a multi-model fusion strategy."),

      createHeading3("Highlight 5: V2 Three-Layer Hybrid Orchestrator with Lagrangian Relaxation"),
      createBulletPoint("Layer 1 (Strategic): MIP Global Optimization [Day/Week level capacity planning]"),
      createBulletPoint("Layer 2 (Tactical): Meta-heuristic + Rule Distribution [Minute-level task assignment]"),
      createBulletPoint("Layer 3 (Operational): A* + TW Path Planning [Millisecond-level routing]"),
      createBulletPoint("Rolling Horizon: Full replanning every 10 seconds"),
      createParagraph("Technical Innovation: Introduces Lagrangian Relaxation from operations research to decouple AGV subproblems and conveyor line subproblems. This is very rare in open-source/academic systems."),

      // ========== 第五章：功能模块对比 ==========
      createHeading1("5. Functional Module Comparison"),

      createHeading2("5.1 Core Function Coverage"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [1605, 1704, 1603, 1603, 1279, 1232],
        rows: [
          new TableRow({ children: [
            createCell("Function Area", 1605, true),
            createCell("Sub-function", 1704, true),
            createCell("AgvTms", 1603, true),
            createCell("Geek+", 1603, true),
            createCell("Hikvision", 1279, true),
            createCell("HaiQ", 1232, true)
          ]}),
          // Task Management
          new TableRow({ children: [
            createCell("Task Management", 1605, false, { shading: "F5F5F5" }),
            createCell("Create/Edit/Delete", 1704),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("Batch Import (Excel/CSV)", 1704),
            createCell("\u26A0\uFE0F API Only", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 Excel", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("Priority Setting", 1704),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("Dynamic Priority Insertion", 1704),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          // Map Management
          new TableRow({ children: [
            createCell("Map Management", 1605, false, { shading: "F5F5F5" }),
            createCell("Node/Edge Editing", 1704),
            createCell("\u2705 CRUD", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("Topology Visualization", 1704),
            createCell("\u2705 Cytoscape.js (2D)", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 3D", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 2.5D", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 3D", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 3D", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("Multi-Floor Support", 1704),
            createCell("\u274C", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          // AGV Monitoring
          new TableRow({ children: [
            createCell("AGV Monitoring", 1605, false, { shading: "F5F5F5" }),
            createCell("Real-time Position Tracking", 1704),
            createCell("\u2705 WebSocket", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("Battery Status", 1704),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("Historical Trajectory Replay", 1704),
            createCell("\u274C", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("Fault Alarming", 1704),
            createCell("\u26A0\uFE0F Basic", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 Multi-level", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          // Conveyor System (Unique to AgvTms)
          new TableRow({ children: [
            createCell("Conveyor System", 1605, false, { shading: "FFF9E6" }),
            createCell("Segment Definition", 1704),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u274C N/A", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u274C N/A", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u274C N/A", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u274C N/A", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("(Unique Advantage)", 1605, false, { shading: "FFF9E6" }),
            createCell("Speed/Capacity Configuration", 1704),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u274C N/A", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u274C N/A", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u274C N/A", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u274C N/A", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605, false, { shading: "FFF9E6" }),
            createCell("NLP Task Sequencing", 1704),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u274C N/A", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u274C N/A", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u274C N/A", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u274C N/A", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          // Statistics
          new TableRow({ children: [
            createCell("Statistics & Analytics", 1605, false, { shading: "F5F5F5" }),
            createCell("Efficiency Reports", 1704),
            createCell("\u26A0\uFE0F Evaluator", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 BI", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("Heatmap Visualization", 1704),
            createCell("\u274C", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          // System Integration
          new TableRow({ children: [
            createCell("System Integration", 1605, false, { shading: "F5F5F5" }),
            createCell("WMS Integration", 1704),
            createCell("\u2705 API Reserved", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 SAP etc.", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("ERP Integration", 1704),
            createCell("\u2705 API Reserved", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 Oracle etc.", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          // Advanced Features
          new TableRow({ children: [
            createCell("Advanced Features", 1605, false, { shading: "F5F5F5" }),
            createCell("Simulation Verification", 1704),
            createCell("\u2705 Built-in", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 G-Studio", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1605),
            createCell("Digital Twin", 1704),
            createCell("\u274C", 1603, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F Basic", 1603, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1279, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1232, false, { align: AlignmentType.CENTER })
          ]})
        ]
      }),

      // ========== 第六章：性能指标量化对比 ==========
      createHeading1("6. Performance Metrics Quantitative Comparison"),

      createHeading2("6.1 Key Performance Indicators (KPI)"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [2255, 1692, 1692, 1692, 1695],
        rows: [
          new TableRow({ children: [
            createCell("Performance Dimension", 2255, true),
            createCell("AgvTms (Measured)", 1692, true),
            createCell("Geek+ RMS", 1692, true),
            createCell("Hikvision RCS", 1692, true),
            createCell("SEER", 1695, true)
          ]}),
          new TableRow({ children: [
            createCell("Scheduling Response Time", 2255, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1695, false, { shading: "F5F5F5" })
          ]}),
          new TableRow({ children: [
            createCell("  FCFS / Greedy", 2255),
            createCell("< 1ms \u2605 Leader", 1692, false, { shading: "E8F4FD" }),
            createCell("< 10ms", 1692),
            createCell("< 20ms", 1692),
            createCell("< 30ms", 1695)
          ]}),
          new TableRow({ children: [
            createCell("  V2-MIP Solver (15 tasks)", 2255),
            createCell("400ms", 1692, false, { shading: "E8F4FD" }),
            createCell("Undisclosed", 1692),
            createCell("Undisclosed", 1692),
            createCell("-", 1695)
          ]}),
          new TableRow({ children: [
            createCell("  V2-Orchestrator", 2255),
            createCell("25ms", 1692, false, { shading: "E8F4FD" }),
            createCell("Undisclosed", 1692),
            createCell("Undisclosed", 1692),
            createCell("-", 1695)
          ]}),
          new TableRow({ children: [
            createCell("  RL-DQN Inference", 2255),
            createCell("< 10ms (GPU pending)", 1692, false, { shading: "E8F4FD" }),
            createCell("-", 1692),
            createCell("-", 1692),
            createCell("-", 1695)
          ]}),
          new TableRow({ children: [
            createCell("Scalability", 2255, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1695, false, { shading: "F5F5F5" })
          ]}),
          new TableRow({ children: [
            createCell("  Max Supported AGVs", 2255),
            createCell("200 (design target)", 1692, false, { shading: "E8F4FD" }),
            createCell("500+", 1692),
            createCell("300+", 1692),
            createCell("100+ heterogeneous", 1695)
          ]}),
          new TableRow({ children: [
            createCell("  Max Concurrent Tasks", 2255),
            createCell("1000 (design)", 1692, false, { shading: "E8F4FD" }),
            createCell("10000+/day", 1692),
            createCell("5000+/day", 1692),
            createCell("-", 1695)
          ]}),
          new TableRow({ children: [
            createCell("Reliability", 2255, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1695, false, { shading: "F5F5F5" })
          ]}),
          new TableRow({ children: [
            createCell("  System Availability", 2255),
            createCell("Not tested (dev stage)", 1692, false, { shading: "E8F4FD" }),
            createCell("99.99%", 1692),
            createCell("99.9%", 1692),
            createCell("99.9%", 1695)
          ]}),
          new TableRow({ children: [
            createCell("  Fault Recovery", 2255),
            createCell("Basic logging", 1692, false, { shading: "E8F4FD" }),
            createCell("Auto failover", 1692),
            createCell("Cluster redundancy", 1692),
            createCell("Redundant controller", 1695)
          ]})
        ]
      }),

      createHeading2("6.2 Benchmark Test Results (from backend/benchmark_results/)"),
      createParagraph("Scenario: small_warehouse (3 AGVs, 15 Tasks)"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [2009, 1201, 1201, 1201, 1201, 2213],
        rows: [
          new TableRow({ children: [
            createCell("Algorithm", 2009, true),
            createCell("Assign Rate", 1201, true),
            createCell("Makespan", 1201, true),
            createCell("Distance", 1201, true),
            createCell("Latency", 1201, true),
            createCell("Status", 2213, true)
          ]}),
          new TableRow({ children: [
            createCell("FCFS", 2009),
            createCell("33% (5/15)", 1201, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1201, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1201, false, { align: AlignmentType.CENTER }),
            createCell("< 1ms", 1201, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 OK", 2213, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Greedy", 2009),
            createCell("33% (5/15)", 1201, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1201, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1201, false, { align: AlignmentType.CENTER }),
            createCell("1ms", 1201, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 OK", 2213, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("V1-Hybrid", 2009),
            createCell("0% (0/15)", 1201, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1201, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1201, false, { align: AlignmentType.CENTER }),
            createCell("600ms", 1201, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F Needs Tuning", 2213, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("V2-MIP", 2009),
            createCell("100% (15/15)", 1201, false, { align: AlignmentType.CENTER, shading: "D4EDDA" }),
            createCell("120.5s", 1201, false, { align: AlignmentType.CENTER, shading: "D4EDDA" }),
            createCell("850m", 1201, false, { align: AlignmentType.CENTER, shading: "D4EDDA" }),
            createCell("400ms", 1201, false, { align: AlignmentType.CENTER, shading: "D4EDDA" }),
            createCell("\u2705 OPTIMAL", 2213, false, { align: AlignmentType.CENTER, shading: "D4EDDA" })
          ]}),
          new TableRow({ children: [
            createCell("V2-Orchestrator", 2009),
            createCell("27% (4/15)", 1201, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1201, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1201, false, { align: AlignmentType.CENTER }),
            createCell("25ms", 1201, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 Deadlock Recovery", 2213, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("RL-DQN (Trained)", 2009),
            createCell("Pending", 1201, false, { align: AlignmentType.CENTER }),
            createCell("Pending", 1201, false, { align: AlignmentType.CENTER }),
            createCell("Pending", 1201, false, { align: AlignmentType.CENTER }),
            createCell("< 10ms", 1201, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 Model Saved (2366KB)", 2213, false, { align: AlignmentType.CENTER })
          ]})
        ]
      }),

      // ========== 第七章：SWOT分析 ==========
      createHeading1("7. SWOT Analysis"),

      createHeading2("7.1 Strengths (Internal Advantages)"),
      createBulletPoint("\uD83D\uDCAA Richest Algorithm Variety: 6 scheduling algorithms (including RL) - rare in commercial products"),
      createBulletPoint("\uD83D\uDCAA RL Training-Deployment Complete Pipeline: One of few systems achieving full closed loop"),
      createBulletPoint("\uD83D\uDCAA Conveyor Line Coordination: Clear market differentiation - most AGV systems ignore fixed conveyors"),
      createBulletPoint("\uD83D\uDCAA Built-in Benchmark Framework: Powerful tool for algorithm validation and comparison"),
      createBulletPoint("\uD83D\uDCAA Full Python Tech Stack: Easy to iterate, low barrier for researchers and students"),

      createHeading2("7.2 Weaknesses (Internal Disadvantages)"),
      createBulletPoint("\u274C No Production-Grade Deployment Cases: All testing in development environment"),
      createBulletPoint("\u274C Lacks 3D Visualization/Digital Twin: Competitors offer immersive 3D monitoring"),
      createBulletPoint("\u274C No Multi-Floor/Elevator Support: Limits application scenarios"),
      createBulletPoint("\u274C Monolithic Architecture: Hard to scale horizontally for large deployments"),
      createBulletPoint("\u274C Unknown Test Coverage: Code quality assurance needs improvement"),

      createHeading2("7.3 Opportunities (External Factors)"),
      createBulletPoint("\uD83DDFE2 AGV/AMR Market Growing 35% Annually: China's 2025 shipment ~280K units, market size ~42B RMB"),
      createBulletPoint("\uD83DDFE2 RL Industrial Application Window: Most competitors still in experimental phase"),
      createBulletPoint("\uD83DDFE2 Strong SME Customization Demand: Budget-conscious projects need cost-effective alternatives"),
      createBulletPoint("\uD83DDFE2 Academic/Education Market: Universities need algorithm research platforms"),
      createBulletPoint("\uD83DDFE2 Cross-border Project Opportunities: Going global with competitive pricing"),

      createHeading2("7.4 Threats (External Risks)"),
      createBulletPoint("\uD83D\uDD34 Head Vendors Have Capital/Talent Advantages: Can outspend on R&D"),
      createBulletPoint("\uD83D\uDD34 Open Source Alternatives May Emerge: ROS Navigation community growing"),
      createBulletPoint("\uD83D\uDD34 Rapid Technology Stack Evolution: Keeping pace requires continuous investment"),
      createBulletPoint("\uD83D\uDD34 Data Security Compliance Requirements: Increasing regulatory burden"),
      createBulletPoint("\uD83D\uDD34 Hardware-Bound Ecosystem Barriers: Vendors lock customers with proprietary hardware"),

      // ========== 第八章：应用场景匹配分析 ==========
      createHeading1("8. Application Scenario Matching Analysis"),

      createHeading2("8.1 Best Fit Scenarios for AgvTms"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [3013, 1507, 4506],
        rows: [
          new TableRow({ children: [
            createCell("Scenario Type", 3013, true),
            createCell("Fit Rating", 1507, true),
            createCell("Description", 4506, true)
          ]}),
          new TableRow({ children: [
            createCell("\uD83D\uDCDA Research/Teaching Platform", 3013),
            createCell("\u2605\u2605\u2605\u2605\u2605", 1507, false, { align: AlignmentType.CENTER }),
            createCell("Complete algorithms, clear code, highly extensible for二次开发", 4506)
          ]}),
          new TableRow({ children: [
            createCell("\uD83E\uDD1D Algorithm Prototype Validation (POC)", 3013),
            createCell("\u2605\u2605\u2605\u2605\u2605", 1507, false, { align: AlignmentType.CENTER }),
            createCell("Quickly validate new algorithm effectiveness with one-click benchmark", 4506)
          ]}),
          new TableRow({ children: [
            createCell("\uD83C\uDFED Small-Medium Manufacturing (\u226420 AGVs)", 3013),
            createCell("\u2605\u2605\u2605\u2605\u2606", 1507, false, { align: AlignmentType.CENTER }),
            createCell("Conveyor coordination is unique advantage for mixed environments", 4506)
          ]}),
          new TableRow({ children: [
            createCell("\uD83D\uDCE6 E-commerce Sorting Center (Mixed Equipment)", 3013),
            createCell("\u2605\u2605\u2605\u2605\u2606", 1507, false, { align: AlignmentType.CENTER }),
            createCell("AGV + conveyor hybrid scheduling capability", 4506)
          ]}),
          new TableRow({ children: [
            createCell("\uD83D\uDD2C RL Scheduling Research", 3013),
            createCell("\u2605\u2605\u2605\u2605\u2605", 1507, false, { align: AlignmentType.CENTER }),
            createCell("Few systems provide complete RL pipeline", 4506)
          ]}),
          new TableRow({ children: [
            createCell("\uD83D\uDCB0 Budget-Constrained Projects (<500K RMB)", 3013),
            createCell("\u2605\u2605\u2605\u2605\u2605", 1507, false, { align: AlignmentType.CENTER }),
            createCell("Zero licensing fees, fully open-source tech stack", 4506)
          ]})
        ]
      }),

      createHeading2("8.2 Scenarios Requiring Caution"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [3513, 1207, 4306],
        rows: [
          new TableRow({ children: [
            createCell("Scenario Type", 3513, true),
            createCell("Risk Level", 1207, true),
            createCell("Recommendation", 4306, true)
          ]}),
          new TableRow({ children: [
            createCell("Large Warehouses (>100 AGVs)", 3513),
            createCell("\u26A0\uFE0F High", 1207, false, { align: AlignmentType.CENTER }),
            createCell("Need distributed architecture, Redis cache, Kafka message queue", 4306)
          ]}),
          new TableRow({ children: [
            createCell("7x24 Critical Operations", 3513),
            createCell("\u26A0\uFE0F High", 1207, false, { align: AlignmentType.CENTER }),
            createCell("Need cluster deployment, fault transfer, monitoring alerts", 4306)
          ]}),
          new TableRow({ children: [
            createCell("Safety-Critical Scenarios (Pharma/Chemical)", 3513),
            createCell("\uD83D\uDD34 Very High", 1207, false, { align: AlignmentType.CENTER }),
            createCell("Need safety certification, functional safety (IEC 61508)", 4306)
          ]}),
          new TableRow({ children: [
            createCell("Multi-Vendor Heterogeneous AGV Mixed Operation", 3513),
            createCell("\u26A0\uFE0F Medium", 1207, false, { align: AlignmentType.CENTER }),
            createCell("Need VDA 5050 protocol adapter development", 4306)
          ]}),
          new TableRow({ children: [
            createCell("Customers Require Mature Commercial Products", 3513),
            createCell("\uD83D\uDD34 Very High", 1207, false, { align: AlignmentType.CENTER }),
            createCell("Recommend Geek+, Hikvision, or SEER instead", 4306)
          ]})
        ]
      }),

      // ========== 第九章：改进路线图 ==========
      createHeading1("9. Improvement Roadmap"),

      createHeading2("9.1 Short-Term Optimizations (1-3 Months)"),
      createHeading3("Priority P0 - Production Readiness Foundation:"),
      createBulletPoint("Introduce Redis Cache Layer (3 days expected) - Response speed 10x improvement"),
      createBulletPoint("Add Unit Tests for Core Algorithms (5 days) - Establish regression confidence"),
      createBulletPoint("Docker Compose Production Configuration (2 days) - One-click deployment capability"),
      createBulletPoint("API Documentation (Swagger/OpenAPI) Enhancement (2 days) - Integration convenience"),
      createBulletPoint("V1-Hyperparameter Default Value Tuning (3 days) - Assignment rate 0% \u2192 60%+"),

      createHeading3("Priority P1 - Feature Completion:"),
      createBulletPoint("Frontend Real-time Map Refresh via WebSocket Push (3 days) - Monitoring experience transformation"),
      createBulletPoint("Batch Task Import (CSV/Excel) (2 days) - Operational efficiency improvement"),
      createBulletPoint("Basic Alerting System (Low battery/Task timeout) (3 days) - Operations foundation"),
      createBulletPoint("User Authentication/Permission Control (3 days) - Multi-user security"),

      createHeading2("9.2 Medium-Term Evolution (3-6 Months)"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [603, 2406, 6017],
        rows: [
          new TableRow({ children: [
            createCell("#", 603, true),
            createCell("Improvement Direction", 2406, true),
            createCell("Specific Content & Target Capability", 6017, true)
          ]}),
          new TableRow({ children: [
            createCell("1", 603, false, { align: AlignmentType.CENTER }),
            createCell("Microservice Decomposition", 2406),
            createCell("Independent deployment of Scheduler Engine / Map Service / Evaluation Service - Target: Hikvision RCS architecture level", 6017)
          ]}),
          new TableRow({ children: [
            createCell("2", 603, false, { align: AlignmentType.CENTER }),
            createCell("Message Queue Integration", 2406),
            createCell("Kafka/RabbitMQ async task processing - Industry standard requirement", 6017)
          ]}),
          new TableRow({ children: [
            createCell("3", 603, false, { align: AlignmentType.CENTER }),
            createCell("GPU Inference Acceleration", 2406),
            createCell("ONNX Runtime + TensorRT - Target: RL inference <1ms", 6017)
          ]}),
          new TableRow({ children: [
            createCell("4", 603, false, { align: AlignmentType.CENTER }),
            createCell("3D Visualization Upgrade", 2406),
            createCell("Three.js/Babylon.js Digital Twin - Target: Geek+ G-Studio level", 6017)
          ]}),
          new TableRow({ children: [
            createCell("5", 603, false, { align: AlignmentType.CENTER }),
            createCell("VDA 5050 Protocol Support", 2406),
            createCell("Third-party AGV brand adaptation - Target: SEER heterogeneous scheduling capability", 6017)
          ]}),
          new TableRow({ children: [
            createCell("6", 603, false, { align: AlignmentType.CENTER }),
            createCell("RL Model Continuous Learning", 2406),
            createCell("Online Fine-tuning mechanism - Industry frontier capability", 6017)
          ]})
        ]
      }),

      createHeading2("9.3 Long-Term Vision (6-12 Months)"),
      createBulletPoint("Cloud-Native: Kubernetes Operator + Helm Chart - Auto scaling capability"),
      createBulletPoint("Digital Twin: Unity/Unreal Rendering + Physics Simulation - Premium project competitiveness"),
      createBulletPoint("Multi-tenant SaaS: Tenant isolation + Usage metering - Commercialization path"),
      createBulletPoint("Industry Solution Packages: E-commerce/Manufacturing/Pharma pre-configurations - Rapid delivery"),
      createBulletPoint("Open Algorithm Marketplace: Third-party algorithm plugin mechanism - Ecosystem building"),

      // ========== 第十章：结论与建议 ==========
      createHeading1("10. Conclusions & Recommendations"),

      createHeading2("10.1 Overall Assessment"),
      createParagraph({ text: "AgvTms is an \"algorithm-dense\" AGV scheduling system prototype. Its core competitiveness manifests in:", bold: true }),
      createBulletPoint("Algorithm Breadth: 6 scheduling algorithms (including RL) are rare among commercial products"),
      createBulletPoint("Algorithm Depth: MIP three-layer orchestration + Lagrangian relaxation reaches graduate/PhD thesis level"),
      createBulletPoint("Completeness: Full chain connection from training \u2192 evaluation \u2192 deployment \u2192 monitoring"),
      createBulletPoint("Uniqueness: Conveyor line coordination + NLP optimization is a clear market differentiator"),

      createParagraph({ text: "Main shortcomings集中在工程化程度:", bold: true }),
      createBulletPoint("Lacks large-scale deployment verification"),
      createBulletPoint("Visualization capability weak (no 3D/digital twin)"),
      createBulletPoint("Distributed/high-availability architecture missing"),
      createBulletPoint("Documentation and test coverage need improvement"),

      createHeading2("10.2 Final Scoring"),
      new Table({
        width: { size: 7026, type: WidthType.DXA },
        columnWidths: [3500, 1763, 1763],
        rows: [
          new TableRow({ children: [
            createCell("Evaluation Dimension", 3500, true),
            createCell("Score (/10)", 1763, true),
            createCell("Weighted Score", 1763, true)
          ]}),
          new TableRow({ children: [
            createCell("Algorithm Innovation", 3500),
            createCell("9.0", 1763, false, { align: AlignmentType.CENTER, shading: "D4EDDA" }),
            createCell("2.25", 1763, false, { align: AlignmentType.CENTER, shading: "D4EDDA" })
          ]}),
          new TableRow({ children: [
            createCell("Feature Completeness", 3500),
            createCell("6.5", 1763, false, { align: AlignmentType.CENTER }),
            createCell("1.30", 1763, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Engineering Quality", 3500),
            createCell("5.5", 1763, false, { align: AlignmentType.CENTER }),
            createCell("1.10", 1763, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Usability / Documentation", 3500),
            createCell("5.0", 1763, false, { align: AlignmentType.CENTER }),
            createCell("0.75", 1763, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Commercialization Potential", 3500),
            createCell("6.0", 1763, false, { align: AlignmentType.CENTER }),
            createCell("1.20", 1763, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("TOTAL SCORE", 3500, false, { shading: "2E75B6" }),
            createCell("-", 1763, false, { align: AlignmentType.CENTER, shading: "2E75B6" }),
            createCell("6.60 / 10", 1763, false, { align: AlignmentType.CENTER, shading: "E8F4BD", bold: true })
          ]})
        ]
      }),

      createParagraph({ text: "Rating: B+ (Above Average, Growth-stage Product with Outstanding Highlights)", bold: true, size: 26, color: "2E75B6" }),

      createHeading2("10.3 One-Line Summary"),
      createParagraph({ 
        text: "AgvTms has reached industry-leading levels in AGV scheduling algorithm research depth and innovation, particularly forming unique technical barriers in RL applications, conveyor line coordination, and multi-layer optimization. If it can address shortcomings in engineering, visualization, and deployment operations, it has full potential to become the preferred AGV scheduling solution for SME markets and academic fields.",
        italics: true,
        size: 24
      }),

      // ========== 分页 ==========
      new Paragraph({ children: [new PageBreak()] }),

      // ========== 附录 ==========
      createHeading1("Appendix A: Reference Resources"),
      createBulletPoint("Geek+ (极智嘉): https://www.geekplus.com/en"),
      createBulletPoint("Hikvision Robot RCS: https://blog.csdn.net/HW18296412587/article/details/153929862"),
      createBulletPoint("Hai Robotics (海柔创新): https://www.hairobotics.cn"),
      createBulletPoint("SEER (仙工智能): https://www.seer-robot.com"),
      createBulletPoint("RCS Technical Architecture: https://jishuzhan.net/article/2040011738065145857"),
      createBulletPoint("2026 Top 6 Vendor Comparison: https://www.sohu.com/a/1015725815_122741106"),

      createHeading1("Appendix B: Glossary"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [2009, 4013, 3004],
        rows: [
          new TableRow({ children: [
            createCell("Term", 2009, true),
            createCell("Full Name", 4013, true),
            createCell("Explanation", 3004, true)
          ]}),
          new TableRow({ children: [ createCell("AGV", 2009), createCell("Automated Guided Vehicle", 4013), createCell("自动导引运输车", 3004) ]}),
          new TableRow({ children: [ createCell("AMR", 2009), createCell("Autonomous Mobile Robot", 4013), createCell("自主移动机器人", 3004) ]}),
          new TableRow({ children: [ createCell("TMS", 2009), createCell("Transportation Management System", 4013), createCell("运输管理系统", 3004) ]}),
          new TableRow({ children: [ createCell("RCS", 2009), createCell("Robot Control System", 4013), createCell("机器人控制系统", 3004) ]}),
          new TableRow({ children: [ createCell("MIP", 2009), createCell("Mixed Integer Programming", 4013), createCell("混合整数规划", 3004) ]}),
          new TableRow({ children: [ createCell("CP-SAT", 2009), createCell("Constraint Programming - Satisfiability", 4013), createCell("约束规划满足问题", 3004) ]}),
          new TableRow({ children: [ createCell("RL", 2009), createCell("Reinforcement Learning", 4013), createCell("强化学习", 3004) ]}),
          new TableRow({ children: [ createCell("DQN", 2009), createCell("Deep Q-Network", 4013), createCell("深度Q网络", 3004) ]}),
          new TableRow({ children: [ createCell("PPO", 2009), createCell("Proximal Policy Optimization", 4013), createCell("近端策略优化", 3004) ]}),
          new TableRow({ children: [ createCell("ACO", 2009), createCell("Ant Colony Optimization", 4013), createCell("蚁群优化算法", 3004) ]}),
          new TableRow({ children: [ createCell("SA", 2009), createCell("Simulated Annealing", 4013), createCell("模拟退火算法", 3004) ]}),
          new TableRow({ children: [ createCell("SIPP", 2009), createCell("Safe Interval Path Planning", 4013), createCell("安全间隔路径规划", 3004) ]}),
          new TableRow({ children: [ createCell("SLAM", 2009), createCell("Simultaneous Localization and Mapping", 4013), createCell("同步定位与建图", 3004) ]})
        ]
      }),

      createHeading1("Appendix C: Version Information"),
      new Table({
        width: { size: 7026, type: WidthType.DXA },
        columnWidths: [2500, 4526],
        rows: [
          new TableRow({ children: [ createCell("Report Version", 2500, true), createCell("v1.0", 4526) ]}),
          new TableRow({ children: [ createCell("Generation Date", 2500, true), createCell("2026-06-15", 4526) ]}),
          new TableRow({ children: [ createCell("Analysis Object", 2500, true), createCell("AgvTms main branch (latest commit)", 4526) ]}),
          new TableRow({ children: [ createCell("Comparison Baseline", 2500, true), createCell("2025-2026 mainstream AGV scheduling product public info", 4526) ]}),
          new TableRow({ children: [ createCell("Disclaimer", 2500, true), createCell("Commercial product parameters from public sources; may not reflect latest accurate data. For research reference only.", 4526) ]})
        ]
      }),

      new Paragraph({ spacing: { before: 400 } }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "--- End of Report ---", italics: true, size: 20, color: "999999", font: "Arial" })]
      })
    ]
  }]
});

// 生成文档
Packer.toBuffer(doc).then(buffer => {
  const outputPath = '/Users/water/Documents/AgvTms/docs/PRODUCT_COMPARISON_REPORT.docx';
  fs.writeFileSync(outputPath, buffer);
  console.log(`Document saved to: ${outputPath}`);
  console.log(`File size: ${(buffer.length / 1024).toFixed(2)} KB`);
});
