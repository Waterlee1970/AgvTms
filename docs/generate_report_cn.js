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
        font: "\u5B8B\u4F53"
      })]
    })]
  });
}

// 创建标题段落
function createHeading1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 400, after: 200 },
    children: [new TextRun({ text: text, bold: true, size: 32, font: "\u5B8B\u4F53", color: "2E75B6" })]
  });
}

function createHeading2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 300, after: 150 },
    children: [new TextRun({ text: text, bold: true, size: 28, font: "\u5B8B\u4F53", color: "1F4E79" })]
  });
}

function createHeading3(text) {
  return new Paragraph({
    spacing: { before: 200, after: 100 },
    children: [new TextRun({ text: text, bold: true, size: 24, font: "\u5B8B\u4F53", color: "2E75B6" })]
  });
}

function createParagraph(text, options = {}) {
  return new Paragraph({
    spacing: { before: 100, after: 100 },
    alignment: options.align || AlignmentType.LEFT,
    indent: options.indent ? { firstLine: 480 } : undefined,
    children: [new TextRun({
      text: text,
      size: 22,
      font: "\u5B8B\u4F53",
      bold: options.bold,
      color: options.color || "333333"
    })]
  });
}

function createBulletPoint(text, reference = "bullets") {
  return new Paragraph({
    numbering: { reference: reference, level: 0 },
    spacing: { before: 60, after: 60 },
    children: [new TextRun({ text: text, size: 22, font: "\u5B8B\u4F53" })]
  });
}

// 创建文档
const doc = new Document({
  styles: {
    default: { document: { run: { font: "\u5B8B\u4F53", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "\u5B8B\u4F53", color: "2E75B6" },
        paragraph: { spacing: { before: 400, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: "\u5B8B\u4F53", color: "1F4E79" },
        paragraph: { spacing: { before: 300, after: 150 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "\u5B8B\u4F53", color: "2E75B6" },
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
          children: [new TextRun({ text: "AGV-TMS \u4EA7\u54C1\u7CFB\u7EDF\u5BF9\u6BD4\u5206\u6790\u62A5\u544A", italics: true, size: 18, color: "666666", font: "\u5B8B\u4F53" })]
        })]
      })
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ text: "\u7B2C ", size: 18, font: "\u5B8B\u4F53" }),
            new TextRun({ children: [PageNumber.CURRENT], size: 18, font: "\u5B8B\u4F53" }),
            new TextRun({ text: " \u9875 | \u673A\u5BC6", size: 18, color: "999999", font: "\u5B8B\u4F53" })
          ]
        })]
      })
    },
    children: [
      // ========== 封面 ==========
      new Paragraph({ spacing: { before: 2000 } }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "AGV-TMS", bold: true, size: 72, font: "\u5B8B\u4F53", color: "2E75B6" })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 200 },
        children: [new TextRun({ text: "\u4EA7\u54C1\u7CFB\u7EDF\u4E0E\u540C\u7C7B\u4EA7\u54C1\u5BF9\u6BD4\u5206\u6790\u62A5\u544A", size: 36, font: "\u5B8B\u4F53", color: "1F4E79" })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 100 },
        children: [new TextRun({ text: "AGV\u8C03\u5EA6\u7BA1\u7406\u7CFB\u7EDF\u5E02\u573A\u7ADE\u4E89\u529B\u5206\u6790", size: 28, font: "\u5B8B\u4F53", color: "666666" })]
      }),
      new Paragraph({ spacing: { before: 800 } }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "\u62A5\u544A\u7248\u672C\uff1Av1.0", size: 24, font: "\u5B8B\u4F53" })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 100 },
        children: [new TextRun({ text: "\u65E5\u671F\uff1A2026\u5E746\u670815\u65E5", size: 24, font: "\u5B8B\u4F53" })]
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
        children: [new TextRun({ text: "\u5BF9\u6807\u4EA7\u54C1\uff1A\u6781\u667A\u5609RMS | \u6D77\u5EB7RCS | \u6D67\u67D5HAIQ | \u65BA\u89C6\u6CB3\u56FE | \u4ED9\u5DE5\u667A\u80FDSEER", size: 20, font: "\u5B8B\u4F53", color: "666666" })]
      }),

      // ========== 分页 ==========
      new Paragraph({ children: [new PageBreak()] }),

      // ========== 目录 ==========
      new Paragraph({
        heading: HeadingLevel.HEADING_1,
        children: [new TextRun({ text: "\u76EE \u5F55", bold: true, size: 32, font: "\u5B8B\u4F53", color: "2E75B6" })]
      }),
      new TableOfContents("\u76EE\u5F55", { hyperlink: true, headingStyleRange: "1-3" }),

      // ========== 分页 ==========
      new Paragraph({ children: [new PageBreak()] }),

      // ========== 第一章：执行摘要 ==========
      createHeading1("\u4E00\u3001\u6267\u884C\u6458\u8981"),
      createParagraph("本报告对 **AgvTms（AGV Transport Management System，AGV\u8FD0\u8F93\u7BA1\u7406\u7CFB\u7EDF）** \u4E0E\u5F53\u524D\u5E02\u573A\u4E0B\u4E3B\u6D41\u7684AGV/AMR\u8C03\u5EA6\u7BA1\u7406\u7CFB\u7EDF\u8FDB\u884C\u4E86\u5168\u65B9\u4F4D\u5BF9\u6BD4\u5206\u6790\u3002AgvTms\u662F\u4E00\u4E2A**\u7814\u7A76\u578B+\u5DE5\u7A0B\u539F\u578B\u7EA7**\u7684\u6DF7\u5408\u8C03\u5EA6\u7CFB\u7EDF\uFF0C\u5177\u5907\u72EC\u7279\u7684**V1\u5143\u542F\u53D1\u5F0F + V2\u5DE5\u4E1A\u7EA7\u4F18\u5316\u5F15\u64CE\u53CC\u7248\u67267\u67B6\u6784**\uFF0C\u96C6\u6210\u4E86\u5148\u8FDB\u7B97\u6CD5\u5305\u62EC\u5F3A\u5316\u5B66\u4E60(DQN/PPO)\u3001\u6574\u6570\u89C4\u5212(MIP/CP-SAT)\u3001\u4E09\u5C42\u534F\u8C03\u5668\u7B49\u3002", { indent: true }),
      createParagraph({ text: "\u6838\u5FC3\u7ED3\u8BBA\uFF1A", bold: true }),
      createBulletPoint("\u7B97\u6CD5\u4E30\u5BCC\u5EA6\u4E0E\u5B66\u672F\u521B\u65B0\u6027\uFF1A\u5DF2\u8FBE\u5230\u6216\u90E8\u5206\u8D85\u8D8A\u5546\u4E1A\u4EA7\u54C1\u6C34\u5E73"),
      createBulletPoint("\u5DE5\u7A0B\u6210\u719F\u5EA6\uFF1A\u5728\u90E8\u7F72\u89C4\u6A21\u3001\u53EF\u89C6\u5316\u80FD\u529B\u3001\u751F\u4EA7\u7EA7\u53EF\u9760\u6027\u7B49\u65B9\u9762\u4ecd\u6709\u5DEE\u8DDD"),
      createBulletPoint("\u63A8\u8350\u5B9A\u4F4D\uFF1A\u9002\u5408\u4F5C\u4E3A**\u7B97\u6CD5\u9A8C\u8BC1\u5E73\u53F0**\u3001**\u79D1\u7814\u6559\u5B66\u5DE5\u5177**\u6216**\u4E2D\u5C0F\u89C4\u6A21\u5B9A\u5236\u9879\u76EE\u7684\u6838\u5FC3\u6280\u672F\u57FA\u5E95**"),

      // ========== 第二章：竞品格局总览 ==========
      createHeading1("\u4E8C\u3001\u7ADE\u54C1\u683C\u5C40\u603B\u89C8"),
      
      createHeading2("2.1 \u5173\u952E\u7ADE\u4E99\u5BF9\u624B\u77E9\u9635"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [1605, 1704, 2255, 1718, 1746],
        rows: [
          new TableRow({ children: [
            createCell("\u5382\u5546", 1605, true),
            createCell("\u4EA7\u54C1\u540D\u79F0", 1704, true),
            createCell("\u5B9A\u4F4D", 2255, true),
            createCell("\u5178\u578B\u90E8\u7F72\u89C4\u6A21", 1718, true),
            createCell("\u6280\u672F\u8DEF\u7EBF", 1746, true)
          ]}),
          new TableRow({ children: [
            createCell("\u6781\u667A\u5609 (Geek+)", 1605),
            createCell("RMS/WES/IOP", 1704),
            createCell("\u5168\u7403\u4ED3\u50A8\u673A\u5668\u4eba\u9886\u5BFC\u8005", 2255),
            createCell("318\u53F0/\u5355\u4ED3 (ASKUL\u6848\u4F8B)", 1718),
            createCell("\u8D27\u5230\u4ebaP2P\u62E9\u9009 + \u4E91\u539F\u751F\u5E73\u53F0", 1746)
          ]}),
          new TableRow({ children: [
            createCell("\u6D77\u5EB7\u673A\u5668\u4eba", 1605),
            createCell("RCS/RCS-Lite", 1704),
            createCell("\u89C6\u89C9\u4F20\u611F\u5668\u4E00\u4F53\u5316\u5DE8\u5934", 2255),
            createCell("100-500\u53F0/\u5DE5\u5386", 1718),
            createCell("\u6807\u51C6\u5316\u6574\u673A + \u6027\u4EF7\u6BD4\u4F18", 1746)
          ]}),
          new TableRow({ children: [
            createCell("\u6D67\u67D5\u521B\u65B0 (Hai Robotics)", 1605),
            createCell("HAIQ/HaiPick", 1704),
            createCell("\u7BB1\u5F0F\u4ED3\u50A8ACR\u4E13\u5BB6", 2255),
            createCell("50-200\u53F0/\u4ED3\u5E93", 1718),
            createCell("\u9AD8\u5BC6\u5EA6\u5B58\u50A8(11-21\u7C73) + 7\u5929\u5FEB\u901F\u90E8\u7F72", 1746)
          ]}),
          new TableRow({ children: [
            createCell("\u65BA\u89C6\u79D1\u6280", 1605),
            createCell("\u6CB3\u56FE(Hetu)", 1704),
            createCell("AI\u7B97\u6CD5\u5B9A\u4E49\u786C\u4EF6", 2255),
            createCell("\u5927\u578BAS/RS\u7ACB\u5E93", 1718),
            createCell("3A\u65B9\u6848(AS/RS+AMR+AI) + \u6DF1\u5EA6\u5B66\u4E60", 1746)
          ]}),
          new TableRow({ children: [
            createCell("\u4ED9\u5DE5\u667A\u80FD(SEER)", 1605),
            createCell("SRC+\u661F\u4E91", 1704),
            createCell("\u5F00\u653E\u751F\u6001\u5E73\u53F0\u5546", 2255),
            createCell("100+\u5F02\u6784\u6DF7\u8DD1", 1718),
            createCell("VDA 5050\u534F\u8BAE + 1000+\u8F66\u578B\u5E93", 1746)
          ]}),
          new TableRow({ children: [
            createCell("AgvTms (\u672C\u9879\u76EE)", 1605, false, { shading: "E8F4FD" }),
            createCell("TMS v1+v2", 1704, false, { shading: "E8F4FD" }),
            createCell("\u7814\u7A76/\u539F\u578B\u6DF7\u5408\u8C03\u5EA6", 2255, false, { shading: "E8F4FD" }),
            createCell("2-200\u53F0 (\u8BBE\u8BA1\u76EE\u6807)", 1718, false, { shading: "E8F4FD" }),
            createCell("\u5143\u542F\u53D1\u5F0F + MIP + RL + \u4E09\u5C42\u534F\u8C03", 1746, false, { shading: "E8F4FD" })
          ]})
        ]
      }),

      createHeading2("2.2 \u5E02\u573A\u5B9A\u4F4D\u8C61\u9654"),
      createParagraph("AGV\u8C03\u5EA6\u7CFB\u7EDF\u5E02\u573A\u53EF\u4EE5\u6839\u636E\u5DE5\u4E1A\u6210\u719F\u5EA6\u548C\u5B66\u672F\u521B\u65B0\u6027\u5206\u4E3A\u4E24\u4E2A\u4E3B\u8981\u8C61\u9654\uFF1A"),
      createBulletPoint("\u5546\u4E1A\u4EA7\u54C1\u533A\uFF1A\u6781\u667A\u5609RMS\u3001\u6D77\u5EB7RCS\u3001\u6D67\u67D5HAIQ\u3001\u65BA\u89C6\u6CB3\u56FE - \u7279\u70B9\u662F\u9AD8\u6210\u719F\u5EA6\u3001\u7ECF\u8FC7\u9A8C\u8BC1\u7684\u90E8\u7F72\u3001\u4F01\u4E1A\u7EA7\u652F\u6301"),
      createBulletPoint("\u7814\u7A76\u539F\u578B\u533A\uFF1aAgvTms - \u7279\u70B9\u662F\u7B97\u6CD5\u4E30\u5BCC\u3001\u5B66\u672F\u521B\u65B0\u3001\u5FEB\u901F\u8FFD\u4EE3\u80FD\u529B"),
      createParagraph("AgvTms\u5360\u636E\u4E86\u4E00\u4E2A\u72EC\u7279\u7684\u4F4D\u7F6E\uFF0C\u63D0\u4F9B\"\u6BD4\u5F00\u6E90\u66F4\u4E13\u4E1A\uFF0C\u6BD4\u5546\u4E1A\u66F4\u5177\u521B\u65B0\u6027\"\u7684\u80FD\u529B\u3002"),

      // ========== 第三章：技术架构深度对比 ==========
      createHeading1("\u4E09\u3001\u6280\u672F\u67B6\u6784\u6DF1\u5EA6\u5BF9\u6BD4"),

      createHeading2("3.1 \u7CFB\u7EDF\u67B6\u6784\u6A21\u5F0F\u5BF9\u6BD4"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [1804, 1804, 1804, 1804, 1806],
        rows: [
          new TableRow({ children: [
            createCell("\u7EF4\u5EA6", 1804, true),
            createCell("AgvTms", 1804, true),
            createCell("\u6781\u667A\u5609RMS", 1804, true),
            createCell("\u6D77\u5EB7RCS", 1804, true),
            createCell("\u4ED9\u5DE5SEER", 1806, true)
          ]}),
          new TableRow({ children: [
            createCell("\u67B6\u6784\u98CE\u683C", 1804),
            createCell("\u5355\u4F53FastAPI + \u6A21\u5757\u5316\u7B97\u6CD5\u5E93", 1804),
            createCell("\u5FAE\u670D\u52A1\u4E91\u539F\u751F", 1804),
            createCell("\u5FAE\u670D\u52A1Spring Cloud", 1804),
            createCell("\u63A7\u5236\u5668+\u5E73\u53F0\u53CC\u5C42", 1806)
          ]}),
          new TableRow({ children: [
            createCell("\u540E\u7AEF\u6846\u67B6", 1804),
            createCell("Python FastAPI", 1804),
            createCell("Java/Go (\u672A\u516C\u5F00)", 1804),
            createCell("Java Spring Boot", 1804),
            createCell("C++/Java\u6DF7\u5408", 1806)
          ]}),
          new TableRow({ children: [
            createCell("\u524D\u7AEF\u6280\u672F", 1804),
            createCell("React + TypeScript + Ant Design", 1804),
            createCell("Web Portal + G-Studio\u4EFF\u771F", 1804),
            createCell("Vue3 + Element Plus", 1804),
            createCell("Web + HMI\u89E6\u6478\u5C4F", 1806)
          ]}),
          new TableRow({ children: [
            createCell("\u6570\u636E\u5E93", 1804),
            createCell("SQLite (\u5F00\u53D1) / PostgreSQL (\u751F\u4EA7)", 1804),
            createCell("AWS/Azure\u4E91\u6570\u636E\u5E96\u7FA4", 1804),
            createCell("MySQL + Redis + MongoDB", 1804),
            createCell("\u672A\u516C\u5F00", 1806)
          ]}),
          new TableRow({ children: [
            createCell("\u901A\u4FE1\u534F\u8BAE", 1804),
            createCell("REST API + WebSocket", 1804),
            createCell("RESTful + WebSocket + MQTT", 1804),
            createCell("MQTT/WebSocket/Kafka", 1804),
            createCell("VDA 5050 + \u79C1\u6709\u534F\u8BAE", 1806)
          ]}),
          new TableRow({ children: [
            createCell("\u5BB9\u5668\u5316", 1804),
            createCell("Docker Compose", 1804),
            createCell("Kubernetes", 1804),
            createCell("K8s + Istio\u670D\u52A1\u7F51\u683C", 1804),
            createCell("\u8FB9\u7F18\u8BA1\u7B97\u76D2\u5B50", 1806)
          ]})
        ]
      }),

      createHeading2("3.2 AgvTms \u53CC\u7248\u672C\u67B6\u6784 (\u72EC\u7279\u6027)"),
      createParagraph("AgvTms\u6700\u72EC\u7279\u7684\u7279\u70B9\u662F\u5176\u53CC\u7248\u672C\u7B97\u6CD5\u67B6\u6784\uFF1A"),
      createHeading3("V1 \u7B97\u6CD5\u5957\u4EF6 (\u5143\u542F\u53D1\u5F0F)\uFF1A"),
      createBulletPoint("ACO (\u8681\u7FA4\u4F18\u5316): \u591AAGV\u8DEF\u5F84\u89C4\u5212\uFF0C\u57FA\u4E8E\u4FE1\u606F\u7D20\u77E9\u9635\u7684\u6982\u7387\u5F0F\u8DEF\u5F84\u6784\u5EFA"),
      createBulletPoint("SA (\u6A21\u62DF\u9000\u706B): \u4EFB\u52A1\u5230AGV\u7684\u6700\u4F18\u5206\u914D\uFF0C\u91C7\u7528Metropolis\u63A5\u53D7\u51C6\u5219"),
      createBulletPoint("NLP (\u975E\u7EBF\u6027\u89C4\u5212): \u8F93\u9001\u7EBF\u4EFB\u52A1\u6392\u5E8F\u4F18\u5316\uFF0C\u57FA\u4E8ESLSQP\u6C42\u89E3\u5668"),
      createBulletPoint("HybridScheduler: \u4E94\u9636\u6BB5\u6D41\u6C34\u7EBF\uFF0C\u7EC4\u5408\u4E09\u79CD\u7B97\u6CD5"),
      
      createHeading3("V2 \u5DE5\u4E1A\u7EA7\u7B97\u6CD5\u5957\u4EF6\uFF1A"),
      createBulletPoint("HybridOrchestratorV2: \u4E09\u5C42\u534F\u8C03\u67B6\u6784 (\u6218\u7565/\u6216\u672F/\u64CD\u4F5C)"),
      createBulletPoint("MipTaskAssigner: OR-Tools CP-SAT\u6574\u6570\u89C4\u5212\u6C42\u89E3\u5668"),
      createBulletPoint("PredictiveEngine: \u4E09\u7EF4\u9884\u6D4B\u5F15\u64CE (\u4EFB\u52A1/\u5835\u585E/\u7535\u91CF)"),
      createBulletPoint("RLScheduler: Double Dueling DQN + \u4F18\u5148\u7ECF\u9A8C\u56DE\u653E(PER)"),
      createBulletPoint("TrafficController: \u57FA\u4E8E\u533A\u57DF\u7684\u4EA4\u901A\u7BA1\u63A7 + \u6B7B\u9501\u68C0\u6D4B"),

      // ========== 第四章：算法能力详细对比 ==========
      createHeading1("\u56DB\u3001\u7B97\u6CD5\u80FD\u529B\u8BE6\u7EC6\u5BF9\u6BD4"),

      createHeading2("4.1 \u5168\u9762\u7B97\u6CD5\u77E9\u9635"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [2408, 1370, 1370, 1370, 1370, 1138],
        rows: [
          new TableRow({ children: [
            createCell("\u7B97\u6CD5\u7C7B\u522B", 2408, true),
            createCell("AgvTms", 1370, true),
            createCell("\u6781\u667A\u5609", 1370, true),
            createCell("\u6D77\u5EB7", 1370, true),
            createCell("\u65BA\u89C6", 1370, true),
            createCell("\u4ED9\u5DE5", 1138, true)
          ]}),
          new TableRow({ children: [
            createCell("\u542F\u53D1\u5F0F\u89C4\u5219 (FCFS/Greedy)", 2408),
            createCell("\u2705", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1138, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("\u5143\u542F\u53D1\u5F0F (SA/ACO/GA)", 2408),
            createCell("\u2705 SA, ACO, NLP", 1370, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u26A0\uFE0F \u6709\u9650", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 GA", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F", 1138, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("\u6574\u6570\u89C4\u5212 (MIP)", 2408),
            createCell("\u2705 OR-Tools CP-SAT", 1370, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u274C \u672A\u516C\u5F00", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u274C", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F \u53EF\u80FD", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u274C", 1138, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("\u8DEF\u5F84\u89C4\u5212 (A*/SIPP)", 2408),
            createCell("\u2705 \u53CC\u5411A*", 1370, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 TW-A*", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 SLAM\u96C6\u6210", 1138, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("\u5F3A\u5316\u5B66\u4E60 (RL)", 2408),
            createCell("\u2705 DQN + PPO", 1370, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u26A0\uFE0F \u7814\u7A76\u9636\u6BB5", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F \u5B9E\u9A8C\u9636\u6BB5", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 DL\u9884\u6D4B", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u274C", 1138, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("\u9884\u6D4B\u5F15\u64CE", 2408),
            createCell("\u2705 \u4E09\u7EF4\u5EA6", 1370, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F \u57FA\u7840", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 \u6DF1\u5EA6\u5B66\u4E60", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u274C", 1138, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("\u4E09\u5C42\u534F\u8C03", 2408),
            createCell("\u2705 \u6218\u7565/\u6216\u672F/\u64CD\u4F5C", 1370, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 \u5206\u5C42", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1370, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1138, false, { align: AlignmentType.CENTER })
          ]})
        ]
      }),

      createHeading2("4.2 AgvTms \u72EC\u7279\u6280\u672F\u4EAE\u70B9"),
      
      createHeading3("\u4EAE\u70B9\uFF1A\u516D\u5927\u7B97\u6CD5\u7EDF\u4E00\u9002\u914D\u5668\u67B6\u6784"),
      createParagraph("\u4E0E\u5546\u4E1A\u4EA7\u54C1\u901A\u5E38\u53EA\u63D0\u4F9B2-3\u79CD\u53EF\u914D\u7F6E\u7B97\u6CD5\u4E0D\u540C\uFF0CAgvTms\u63D0\u4F9B6\u79CD\u7B97\u6CD5\u53EF\u901A\u8FC7\u7EDF\u4E00\u63A5\u53E3\u5207\u6362\uFF1A"),
      createBulletPoint("FCFS\u9002\u914D\u5668: \u5148\u6765\u5148\u670D\u52A1\u57FA\u7EBF\u65B9\u6848"),
      createBulletPoint("Greedy\u9002\u914D\u5668: \u6700\u8FD1\u90CA\u542F\u53D1\u5F0F"),
      createBulletPoint("V1Hybrid\u9002\u914D\u5668: SA + ACO + NLP\u7EC4\u5408"),
      createBulletPoint("V2Mip\u9002\u914D\u5668: CP-SAT\u6574\u6570\u89C4\u5212\u83B7\u53D6\u6700\u4F18\u89E3"),
      createBulletPoint("V2Orchestrator\u9002\u914D\u5668: \u4E09\u5C42\u534F\u8C03 + \u9884\u6D4B\u5F15\u64CE"),
      createBulletPoint("RLDQN\u9002\u914D\u5668: \u5F3A\u5316\u5B66\u4E60\u4EE3\u7406(\u8BAD\u7EC3/\u63A8\u7406\u53CC\u6A21\u5F0F)"),

      createHeading3("\u4EAE\u70B9\uFF1A\u5185\u7F6E\u573A\u666F\u5316\u8BC4\u4F30\u57FA\u51C6\u6846\u67B6"),
      createParagraph("\u652F\u6301\"\u4E00\u952E\u8DD1\u5168\u7B97\u6CD5\u5BF9\u6BD4\"\uFF0C\u8F93\u51FA\u6307\u6807\u5305\u62EC\uFF1A"),
      createBulletPoint("\u5206\u914D\u6210\u529F\u7387 (%)"),
      createBulletPoint("\u5E73\u5747\u5B8C\u6210\u65F6\u95F4 (makespan)"),
      createBulletPoint("\u603B\u884C\u9A76\u8DDD\u79BB"),
      createBulletPoint("\u7B97\u6CD5\u54CD\u5E94\u5EF6\u8FDF (ms)"),
      createBulletPoint("\u7535\u91CF\u6D88\u8017\u7EDF\u8BA1"),
      createParagraph("\u8FD9\u662F**\u5B66\u672F\u7814\u7A76\u548C\u7B97\u6CD5\u9009\u578B**\u7684\u5229\u5668\uFF0C\u5546\u4E1A\u4EA7\u54C1\u9700\u8981\u989D\u5916\u8D2D\u4E70\u4EFF\u771F\u8F6F\u4EF6\u624D\u80FD\u5B9E\u73B0\u3002", { italics: true }),

      createHeading3("\u4EAE\u70B9\uFF1aRL\u8BAD\u7EC3\u7BA1\u7EBF (\u5B8C\u6574\u95ED\u73AF)"),
      createBulletPoint("Double Dueling DQN + \u4F18\u5148\u7ECF\u9A8C\u56DE\u653E (PER)"),
      createBulletPoint("Action Masking\u673A\u5236\u4FDD\u8BC1\u53EF\u884C\u6027"),
      createBulletPoint("Gymnasium\u6807\u51C6\u73AF\u5883\u63A5\u53E3"),
      createBulletPoint("\u81EA\u52A8\u4FDD\u5B58\u6A21\u578B\u5E76\u63A5\u5165\u63A8\u7406"),
      createParagraph({ text: "\u884C\u4E1A\u73B0\u72B6: ", bold: true }),
      createParagraph("\u622A\u6B222026\u5E74\uFF0C\u5927\u591A\u6570\u5546\u4E1cAGV\u8C03\u5EA6\u7CFB\u7EDF\u4ecd\u7136\u4F9D\u8D56\u4E8E\u89C4\u5219\u5F15\u64CE + \u4F20\u7EDF\u4F18\u5316\u3002RL\u4E3B\u8981\u5728\u5B9E\u9A8C\u5BA4\u9636\u6BB5\u3002AgvTms\u5DF2\u5B9E\u73B0\u5B8C\u6574\u95ED\u73AF\uFF1A\u8BAD\u7EC3 \u2192 \u4FDD\u5B58 \u2192 \u90E8\u7F72 \u2192 \u8BC4\u4F30\u3002"),

      createHeading3("\u4EAE\u70B9\uFF1a\u9884\u6D4B\u5F15\u64CE\u4E09\u7EF4\u5EA6\u8054\u52A8"),
      createBulletPoint("\u4EFB\u52A1\u8D1F\u8F7D\u9884\u6D4B: \u57FA\u4E8E\u5386\u53F2\u6A21\u5F0F\u7684\u672A\u67651\u5C0F\u65F6\u4EFB\u52A1\u91CF\u9884\u4F30"),
      createBulletPoint("\u5835\u585E\u9884\u6D4B: \u533A\u57DF\u7EA7\u522B\u5835\u585E\u6982\u7387\u9884\u6D4B\uFF0C\u63D0\u524D\u7ED5\u884C"),
      createBulletPoint("\u7535\u91CF\u9884\u6D4B: \u5355\u8F66\u5269\u4F59\u5DE5\u4F5C\u65F6\u95F4\u9884\u6D4B\uFF0C\u667A\u80FD\u5145\u7535\u8C03\u5EA6"),
      createParagraph("\u5927\u591A\u6570\u5546\u4E1c\u7CFB\u7EDF\u7684\"\u9884\u6D4B\"\u9650\u4E8E\u7B80\u5355\u7684\u89C4\u5219\u5916\u63A8\u3002AgvTms\u91C7\u7528\u591A\u6A21\u578B\u878D\u5408\u7B56\u7565\u3002"),

      createHeading3("\u4EAE\u70B9\uFF1aV2\u4E09\u5C42\u6DF7\u5408\u534F\u8C03\u5668 + \u62C9\u683C\u6717\u65E5\u677E\u5F1B"),
      createBulletPoint("\u7B2C1\u5C42 (\u6218\u7565\u5C42): MIP\u5168\u5C40\u4F18\u5316 [\u5929/\u5468\u7EA7\u522B\u5BB9\u91CF\u89C4\u5212]"),
      createBulletPoint("\u7B2C2\u5C42 (\u6216\u672F\u5C42): \u5143\u542F\u53D1\u5F0F + \u89C4\u5219\u5206\u53D1 [\u5206\u949F\u7EA7\u4EFB\u52A1\u5206\u914D]"),
      createBulletPoint("\u7B2C3\u5C42 (\u64CD\u4F5C\u5C42): A* + TW\u8DEF\u5F84\u89C4\u5212 [\u6BEB\u79D2\u7EA7\u8DEF\u7531]"),
      createBulletPoint("\u6EDA\u52A8\u65F6\u57DF: \u6BCF10\u79D2\u5168\u91CF\u91CD\u89C4\u5212"),
      createParagraph("**\u6280\u672F\u521B\u65B0**: \u5F15\u5165\u8FD0\u7B56\u5B66\u4E2D\u7684**\u62C9\u683C\u6717\u65E5\u677E\u5F1B**\u6280\u672F\u89E3\u8026AGV\u5B50\u95EE\u9898\u548C\u8F93\u9001\u7EBF\u5B50\u95EE\u9898\uFF0C\u5728\u5F00\u6E90/\u5B66\u672F\u7CFB\u7EDF\u4E2D\u975E\u5E38\u7F55\u89C1\u3002"),

      // ========== 第五章：功能模块对比 ==========
      createHeading1("\u4E94\u3001\u529F\u80FD\u6A21\u5757\u5BF9\u6BD4"),

      createHeading2("5.1 \u6838\u5FC3\u529F\u80FD\u8986\u76D6\u5EA6"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [1704, 2103, 1401, 1401, 1209, 1208],
        rows: [
          new TableRow({ children: [
            createCell("\u529F\u80FD\u57DF", 1704, true),
            createCell("\u5B50\u529F\u80FD", 2103, true),
            createCell("AgvTms", 1401, true),
            createCell("\u6781\u667A\u5609", 1401, true),
            createCell("\u6D77\u5EB7", 1209, true),
            createCell("\u6D67\u67D5", 1208, true)
          ]}),
          // 任务管理
          new TableRow({ children: [
            createCell("\u4EFB\u52A1\u7BA1\u7406", 1704, false, { shading: "F5F5F5" }),
            createCell("\u521B\u5EFA/\u7F16\u8F91/\u5220\u9664", 2103),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1704),
            createCell("\u6279\u91CF\u5BFC\u5165(Excel/CSV)", 2103),
            createCell("\u26A0\uFE0F API", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 Excel", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1704),
            createCell("\u4F18\u5148\u7EA7\u8bbe\u7F6E", 2103),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          // 地图管理
          new TableRow({ children: [
            createCell("\u5730\u56FE\u7BA1\u7406", 1704, false, { shading: "F5F5F5" }),
            createCell("\u8282\u70B9/\u8FB9\u7F16\u8F91", 2103),
            createCell("\u2705 CRUD", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1704),
            createCell("\u62D3\u6253\u53EF\u89C6\u5316", 2103),
            createCell("\u2705 Cytoscape.js (2D)", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 3D", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 2.5D", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 3D", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1704),
            createCell("\u591A\u697C\u5C42\u652F\u6301", 2103),
            createCell("\u274C", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          // AGV监控
          new TableRow({ children: [
            createCell("AGV\u76D1\u63A7", 1704, false, { shading: "F5F5F5" }),
            createCell("\u5B9E\u65F6\u4F4D\u7F6E\u8DDF\u8E2A", 2103),
            createCell("\u2705 WebSocket", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1704),
            createCell("\u7535\u91CF\u72B6\u6001", 2103),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1704),
            createCell("\u5386\u53F2\u8F68\u8FF9\u56DE\u653E", 2103),
            createCell("\u274C", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          // 输送线系统（AgvTms独有）
          new TableRow({ children: [
            createCell("\u8F93\u9001\u7EBF\u7CFB\u7EDF", 1704, false, { shading: "FFF9E6" }),
            createCell("\u6BB5\u5B9A\u4E49", 2103),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u274C \u4E0D\u9002\u7528", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u274C \u4E0D\u9002\u7528", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u274C \u4E0D\u9002\u7528", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("(**\u72EC\u7279\u4F18\u52BF**)", 1704, false, { shading: "FFF9E6" }),
            createCell("\u901F\u5EA6/\u5BB9\u91CF\u914D\u7F6E", 2103),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u274C \u4E0D\u9002\u7528", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u274C \u4E0D\u9002\u7528", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u274C \u4E0D\u9002\u7528", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1704, false, { shading: "FFF9E6" }),
            createCell("NLP\u4EFB\u52A1\u6392\u5E8F", 2103),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u274C \u4E0D\u9002\u7528", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u274C \u4E0D\u9002\u7528", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u274C \u4E0D\u9002\u7528", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          // 高级功能
          new TableRow({ children: [
            createCell("\u9AD8\u7EA7\u529F\u80FD", 1704, false, { shading: "F5F5F5" }),
            createCell("\u4EFF\u771F\u9A8C\u8BC1", 2103),
            createCell("\u2705 \u5185\u7F6E", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 G-Studio", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("", 1704),
            createCell("\u6570\u5B57\u5B5C\u751F", 2103),
            createCell("\u274C", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F \u57FA\u7840", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1208, false, { align: AlignmentType.CENTER })
          ]}),
          // 系统集成
          new TableRow({ children: [
            createCell("\u7CFB\u7EDF\u96C6\u6210", 1704, false, { shading: "F5F5F5" }),
            createCell("WMS\u5BF9\u63A5", 2103),
            createCell("\u2705 API\u9884\u7559", 1401, false, { align: AlignmentType.CENTER, shading: "E8F4FD" }),
            createCell("\u2705 SAP\u7B49", 1401, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1209, false, { align: AlignmentType.CENTER }),
            createCell("\u2705", 1208, false, { align: AlignmentType.CENTER })
          ]})
        ]
      }),

      // ========== 第六章：性能指标量化对比 ==========
      createHeading1("\u516D\u3001\u6027\u80FD\u6307\u6807\u91CF\u5316\u5BF9\u6BD4"),

      createHeading2("6.1 \u5173\u952E\u6027\u80FD\u6307\u6807 (KPI)"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [2605, 1692, 1692, 1692, 1345],
        rows: [
          new TableRow({ children: [
            createCell("\u6027\u80FD\u7EF4\u5EA6", 2605, true),
            createCell("AgvTms (\u5B9E\u6D4B)", 1692, true),
            createCell("\u6781\u667A\u5609RMS", 1692, true),
            createCell("\u6D77\u5EB7RCS", 1692, true),
            createCell("\u4ED5\u5DE5SEER", 1345, true)
          ]}),
          new TableRow({ children: [
            createCell("\u8C03\u5EA6\u54CD\u5E94\u65F6\u95F4", 2605, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1345, false, { shading: "F5F5F5" })
          ]}),
          new TableRow({ children: [
            createCell("  FCFS / Greedy", 2605),
            createCell("< 1ms \u2605 \u9886\u5148", 1692, false, { shading: "E8F4FD" }),
            createCell("< 10ms", 1692),
            createCell("< 20ms", 1692),
            createCell("< 30ms", 1345)
          ]}),
          new TableRow({ children: [
            createCell("  V2-MIP\u6C42\u89E3 (15\u4EFB\u52A1)", 2605),
            createCell("400ms", 1692, false, { shading: "E8F4FD" }),
            createCell("\u672A\u516C\u5F00", 1692),
            createCell("\u672A\u516C\u5F00", 1692),
            createCell("-", 1345)
          ]}),
          new TableRow({ children: [
            createCell("  V2-\u534F\u8C03\u5668", 2605),
            createCell("25ms", 1692, false, { shading: "E8F4FD" }),
            createCell("\u672A\u516C\u5F00", 1692),
            createCell("\u672A\u516C\u5F00", 1692),
            createCell("-", 1345)
          ]}),
          new TableRow({ children: [
            createCell("  RL-DQN\u63A8\u7406", 2605),
            createCell("< 10ms (GPU\u5F85\u542F\u7528)", 1692, false, { shading: "E8F4FD" }),
            createCell("-", 1692),
            createCell("-", 1692),
            createCell("-", 1345)
          ]}),
          new TableRow({ children: [
            createCell("\u6269\u5C55\u6027", 2605, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1345, false, { shading: "F5F5F5" })
          ]}),
          new TableRow({ children: [
            createCell("  \u6700\u5927\u652F\u6301AGV\u6570", 2605),
            createCell("200 (\u8BBE\u8BA1\u76EE\u6807)", 1692, false, { shading: "E8F4FD" }),
            createCell("500+", 1692),
            createCell("300+", 1692),
            createCell("100+ \u5F02\u6784", 1345)
          ]}),
          new TableRow({ children: [
            createCell("\u53EF\u9760\u6027", 2605, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1692, false, { shading: "F5F5F5" }),
            createCell("", 1345, false, { shading: "F5F5F5" })
          ]}),
          new TableRow({ children: [
            createCell("  \u7CFB\u7EDF\u53EF\u7528\u6027", 2605),
            createCell("\u672A\u6D4B\u8BD5 (\u5F00\u53D1\u9636\u6BB5)", 1692, false, { shading: "E8F4FD" }),
            createCell("99.99%", 1692),
            createCell("99.9%", 1692),
            createCell("99.9%", 1345)
          ]})
        ]
      }),

      createHeading2("6.2 Benchmark\u6D4B\u8BD5\u7ED3\u679C (backend/benchmark_results/)"),
      createParagraph("\u573A\u666F: small_warehouse (3 AGVs, 15 \u4EFB\u52A1)"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [2009, 1301, 1301, 1301, 1301, 1812],
        rows: [
          new TableRow({ children: [
            createCell("\u7B97\u6CD5", 2009, true),
            createCell("\u5206\u914D\u7387", 1301, true),
            createCell("Makespan", 1301, true),
            createCell("\u8DDD\u79BB", 1301, true),
            createCell("\u5EF6\u8FDF", 1301, true),
            createCell("\u72B6\u6001", 1812, true)
          ]}),
          new TableRow({ children: [
            createCell("FCFS", 2009),
            createCell("33% (5/15)", 1301, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1301, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1301, false, { align: AlignmentType.CENTER }),
            createCell("< 1ms", 1301, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 \u6B63\u5E38", 1812, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("Greedy", 2009),
            createCell("33% (5/15)", 1301, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1301, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1301, false, { align: AlignmentType.CENTER }),
            createCell("1ms", 1301, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 \u6B63\u5E38", 1812, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("V1-Hybrid", 2009),
            createCell("0% (0/15)", 1301, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1301, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1301, false, { align: AlignmentType.CENTER }),
            createCell("600ms", 1301, false, { align: AlignmentType.CENTER }),
            createCell("\u26A0\uFE0F \u9700\u8C03\u4F18", 1812, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("V2-MIP", 2009),
            createCell("100% (15/15)", 1301, false, { align: AlignmentType.CENTER, shading: "D4EDDA" }),
            createCell("120.5s", 1301, false, { align: AlignmentType.CENTER, shading: "D4EDDA" }),
            createCell("850m", 1301, false, { align: AlignmentType.CENTER, shading: "D4EDDA" }),
            createCell("400ms", 1301, false, { align: AlignmentType.CENTER, shading: "D4EDDA" }),
            createCell("\u2705 \u6700\u4F18", 1812, false, { align: AlignmentType.CENTER, shading: "D4EDDA" })
          ]}),
          new TableRow({ children: [
            createCell("V2-\u534F\u8C03\u5668", 2009),
            createCell("27% (4/15)", 1301, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1301, false, { align: AlignmentType.CENTER }),
            createCell("N/A", 1301, false, { align: AlignmentType.CENTER }),
            createCell("25ms", 1301, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 \u6B7B\u9501\u6062\u590D", 1812, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("RL-DQN (\u5DF2\u8BAD\u7EC3)", 2009),
            createCell("\u5F85\u6D4B\u8BD5", 1301, false, { align: AlignmentType.CENTER }),
            createCell("\u5F85\u6D4B\u8BD5", 1301, false, { align: AlignmentType.CENTER }),
            createCell("\u5F85\u6D4B\u8BD5", 1301, false, { align: AlignmentType.CENTER }),
            createCell("< 10ms", 1301, false, { align: AlignmentType.CENTER }),
            createCell("\u2705 \u6A21\u578B\u5DF2\u4FDD\u5B58(2366KB)", 1812, false, { align: AlignmentType.CENTER })
          ]})
        ]
      }),

      // ========== 第七章：SWOT分析 ==========
      createHeading1("\u4E03\u3001SWOT\u5206\u6790"),

      createHeading2("7.1 \u4F18\u52BF (Strengths - \u5185\u90E8)"),
      createBulletPoint("\uD83D\uDCAA \u7B97\u6CD5\u79CD\u7C7B\u6700\u4E30\u5BCC: 6\u79CD\u8C03\u5EA6\u7B97\u6CD5(\u5305\u62B9RL) - \u5546\u4E1c\u4EA7\u54C1\u7F55\u89C1"),
      createBulletPoint("\uD83D\uDCAA RL\u8BAD\u7EC3-\u90E8\u7F72\u5B8C\u6574\u95ED\u73AF: \u5C11\u6570\u7CFB\u7EDF\u5B9E\u73B0\u5B8C\u6574\u95ED\u73AF"),
      createBulletPoint("\uD83D\uDCAA \u8F93\u9001\u7EBF\u534F\u540C\u8C03\u5EA6: \u660E\u663E\u5E02\u573A\u5DEE\u5F02\u5316 - \u5927\u591A\u6570AGV\u7CFB\u7EDF\u5FFD\u7565\u56FA\u5B9A\u8F93\u9001\u7EBF"),
      createBulletPoint("\uD83D\uDCAA \u5185\u7F6EBenchmark\u6846\u67B6: \u7B97\u6CD5\u9A8C\u8BC1\u548C\u5BF9\u6BD4\u5229\u5668"),
      createBulletPoint("\uD83D\uDCAA \u5168Python\u6280\u672F\u6808: \u6613\u4E8E\u8FFD\u4EE3\uFF0C\u4F4E\u95E8\u69DB\u9002\u5408\u7814\u7A76\u8005\u548C\u5B66\u751F"),

      createHeading2("7.2 \u52A3\u52BF (Weaknesses - \u5185\u90E8)"),
      createBulletPoint("\u274C \u65E0\u751F\u4EA7\u7EA7\u90E8\u7F72\u6848\u4F8B: \u6240\u6709\u6D4B\u8BD5\u5728\u5F00\u53D1\u73AF\u5883"),
      createBulletPoint("\u274C \u7F3A\u4E4F3D\u53EF\u89C6\u5316/\u6570\u5B57\u5B5C\u751F: \u7ADE\u4E89\u5BF9\u624B\u63D0\u4F9B\u6C89\u6D78\u5F0F3D\u76D1\u63A7"),
      createBulletPoint("\u274C \u65E0\u591A\u697C\u5C42/\u7535\u68AF\u652F\u6301: \u9650\u5236\u5E94\u7528\u573A\u666F"),
      createBulletPoint("\u274C \u5355\u4F53\u67B6\u6784: \u5927\u89C4\u6A21\u90E8\u7F72\u96BE\u4EE5\u6C34\u5E73\u6269\u5C55"),
      createBulletPoint("\u274C \u6D4B\u8BD5\u8986\u76D6\u7387\u672A\u77E5: \u4EE3\u7801\u8D28\u91CF\u4FDD\u8BC1\u9700\u63D0\u5347"),

      createHeading2("7.3 \u673A\u4F1A (Opportunities - \u5916\u90E8)"),
      createBulletPoint("\uD83D\uDFE2 AGV/AMR\u5E02\u573A\u5E74\u589E35%: \u4E2D\u56FD2025\u5E74\u51FA\u8D27\u91CF\u7EA628\u4E07\u53F0\uFF0C\u5E02\u573A\u89C4\u6A21\u7A0442\u4EBF\u5143"),
      createBulletPoint("\uD83D\uDFE2 RL\u5DE5\u4E1A\u5316\u5E94\u7528\u7A97\u53E3: \u5927\u591A\u6570\u7ADE\u4E89\u5BF9\u624B\u4ecd\u5728\u5B9E\u9A8C\u9636\u6BB5"),
      createBulletPoint("\uD83D\uDFE2 \u4E2D\u5C0F\u4F01\u4E1A\u5B9A\u5236\u9700\u6C42\u65FA\u76DB: \u9884\u7B97\u6709\u9650\u9879\u76EE\u9700\u6210\u672C\u66FF\u4EE3\u65B9\u6848"),
      createBulletPoint("\uD83D\uDFE2 \u5B66\u672F/\u6559\u80B2\u5E02\u573A: \u5927\u5B66\u9700\u8981\u7B97\u6CD5\u7814\u7A76\u5E73\u53F0"),
      createBulletPoint("\uD83D\uDFE2 \u8DE8\u5883\u9879\u76EE\u673A\u4F1A: \u8D70\u5411\u5168\u7403\u5177\u5907\u7ADE\u4E87\u529B\u4EF7\u683C"),

      createHeading2("7.4 \u5A01\u80C1 (Threats - \u5916\u90E8)"),
      createBulletPoint("\uD83D\uDD34 \u5934\u90E8\u5382\u5546\u8D44\u672C/\u4EBA\u624D\u4F18\u52BF: \u53EF\u5927\u5E45\u8D85\u652FR&D\u6295\u5165"),
      createBulletPoint("\uD83D\uDD34 \u5F00\u6E90\u66FF\u4EE3\u54C1\u53EF\u80FD\u51FA\u73B0: ROS Navigation\u793E\u533A\u589E\u957F"),
      createBulletPoint("\uD83D\uDD34 \u6280\u672F\u6808\u5FEB\u901F\u8FDB\u5316: \u4FDD\u6301\u6B65\u4F30\u9700\u6301\u7EED\u6295\u8D44"),
      createBulletPoint("\uD83D\uDD34 \u6570\u636E\u5B89\u5168\u5408\u89C4\u8981\u6C42: \u589E\u52A0\u7684\u76D1\u7BA1\u8D1F\u62C5"),
      createBulletPoint("\uD83D\uDD34 \u786C\u4EF6\u7ED1\u5B9A\u751F\u6001\u58C1\u58C1: \u5382\u5546\u9501\u5B9A\u5BA2\u6237\u4E8E\u4E13\u6709\u786C\u4EF6"),

      // ========== 第八章：应用场景匹配分析 ==========
      createHeading1("\u516B\u3001\u5E94\u7528\u573A\u666F\u5339\u914D\u5206\u6790"),

      createHeading2("8.1 AgvTms \u6700\u9002\u5408\u7684\u573A\u666F"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [3213, 1507, 4306],
        rows: [
          new TableRow({ children: [
            createCell("\u573A\u666F\u7C7B\u578B", 3213, true),
            createCell("\u9002\u5408\u5EA6", 1507, true),
            createCell("\u8BF4\u660E", 4306, true)
          ]}),
          new TableRow({ children: [
            createCell("\uD83D\uDCDA \u7814\u7A76/\u6559\u5B66\u5E73\u53F0", 3213),
            createCell("\u2605\u2605\u2605\u2605\u2605", 1507, false, { align: AlignmentType.CENTER }),
            createCell("\u7B97\u6CD5\u9F50\u5168\u3001\u4EE3\u7801\u6E05\u6670\u3001\u9AD8\u5EA6\u53EF\u6269\u5C55", 4306)
          ]}),
          new TableRow({ children: [
            createCell("\uD83E\uDD1D \u7B97\u6CD5\u539F\u578B\u9A8C\u8BC1 (POC)", 3213),
            createCell("\u2605\u2605\u2605\u2605\u2605", 1507, false, { align: AlignmentType.CENTER }),
            createCell("\u5FEB\u901F\u9A8C\u8BC1\u65B0\u7B97\u6CD5\u6548\u679C\uFF0C\u4E00\u952EBenchmark\u5BF9\u6BD4", 4306)
          ]}),
          new TableRow({ children: [
            createCell("\uD83C\uDFED \u4E2D\u5C0F\u5236\u9020\u4EA7\u7EBF (\u226420 AGVs)", 3213),
            createCell("\u2605\u2605\u2605\u2605\u2606", 1507, false, { align: AlignmentType.CENTER }),
            createCell("\u8F93\u9001\u7EBF\u534F\u540C\u662F\u72EC\u7279\u4F18\u52BF", 4306)
          ]}),
          new TableRow({ children: [
            createCell("\uD83D\uDCB0 \u9884\u7B97\u6709\u9650\u9879\u76EE (<50\u4E07)", 3213),
            createCell("\u2605\u2605\u2605\u2605\u2605", 1507, false, { align: AlignmentType.CENTER }),
            createCell("\u96F6\u6388\u67E3\u8D39\uFF0C\u5168\u5F00\u6E90\u6280\u672F\u6808", 4306)
          ]})
        ]
      }),

      createHeading2("8.2 \u9700\u8C28\u614E\u8003\u8651\u7684\u573A\u666F"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [3713, 1207, 4106],
        rows: [
          new TableRow({ children: [
            createCell("\u573A\u666F\u7C7B\u578B", 3713, true),
            createCell("\u98CE\u9669\u7EA7\u522B", 1207, true),
            createCell("\u5EFA\u8BAE", 4106, true)
          ]}),
          new TableRow({ children: [
            createCell("\u5927\u578B\u4ED3\u50A8 (>100 AGVs)", 3713),
            createCell("\u26A0\uFE0F \u9AD8", 1207, false, { align: AlignmentType.CENTER }),
            createCell("\u9700\u5206\u5E03\u5F0F\u67B6\u6784\u3001Redis\u7F13\u5B58\u3001Kafka\u6D88\u606F\u961F\u5217", 4106)
          ]}),
          new TableRow({ children: [
            createCell("7\u00D724\u5C0F\u65F6\u5173\u952E\u4E1A\u52A1", 3713),
            createCell("\u26A0\uFE0F \u9AD8", 1207, false, { align: AlignmentType.CENTER }),
            createCell("\u9700\u96C6\u7FA4\u90E8\u7F72\u3001\u6545\u969C\u8F6C\u79FB\u3001\u76D1\u63A7\u544A\u8B66", 4106)
          ]}),
          new TableRow({ children: [
            createCell("\u5B89\u5168\u82DB\u6C42\u573A\u666F (\u533B\u836F/\u5316\u5DE5)", 3713),
            createCell("\uD83D\uDD34 \u5F88\u9AD8", 1207, false, { align: AlignmentType.CENTER }),
            createCell("\u9700\u5B89\u5168\u8BA4\u8BC1\u3001\u529F\u80FD\u5B89\u5168 (IEC 61508)", 4106)
          ]}),
          new TableRow({ children: [
            createCell("\u5BA2\u6237\u8981\u6C42\u6210\u719F\u5546\u4E1A\u4EA7\u54C1", 3713),
            createCell("\uD83D\uDD34 \u5F88\u9AD8", 1207, false, { align: AlignmentType.CENTER }),
            createCell("\u5EFA\u8BAE\u9009\u62E9\u6781\u667A\u5609\u3001\u6D77\u5EB7\u6216\u4ED9\u5DE5", 4106)
          ]})
        ]
      }),

      // ========== 第九章：改进路线图 ==========
      createHeading1("\u4E5D\u3001\u6539\u8FDB\u8DEF\u7EBF\u56FE"),

      createHeading2("9.1 \u77ED\u671F\u4F18\u5316 (1-3\u4E2A\u6708)"),
      createHeading3("\u4F18\u5148\u7EA7P0 - \u751F\u4EA7\u5C31\u7EEA\u57FA\u7840:"),
      createBulletPoint("\u5F15\u5165 Redis \u7F13\u5B58\u5C42 (3\u5929) - \u54CD\u5E94\u901F\u5EA6\u63D0\u534710\u500D"),
      createBulletPoint("\u8865\u5145\u5355\u5143\u6D4B\u8BD5-\u6838\u5FC3\u7B97\u6CD5 (5\u5929) - \u5EFA\u7ACB\u56DE\u5F52\u4FE1\u5FC3"),
      createBulletPoint(" Docker Compose \u751F\u4EA7\u914D\u7F6E (2\u5929) - \u4E00\u952E\u90E8\u7F72\u80FD\u529B"),
      createBulletPoint("API\u6587\u6863 (Swagger/OpenAPI) (2\u5929) - \u96C6\u6210\u4FBF\u5229\u6027"),
      createBulletPoint("V1-Hyper\u53C2\u6570\u9ED8\u8BA4\u503C\u8C03\u4F18 (3\u5929) - \u5206\u914D\u7387 0% \u2192 60%+"),

      createHeading2("9.2 \u4E2D\u671F\u6F14\u8FDB (3-6\u4E2A\u6708)"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [603, 2506, 5917],
        rows: [
          new TableRow({ children: [
            createCell("#", 603, true),
            createCell("\u6539\u8FDB\u65B9\u5411", 2506, true),
            createCell("\u5177\u4F53\u5185\u5BB9 & \u76EE\u6807\u80FD\u529B", 5917, true)
          ]}),
          new TableRow({ children: [
            createCell("1", 603, false, { align: AlignmentType.CENTER }),
            createCell("\u5FAE\u670D\u52A1\u62C6\u5206", 2506),
            createCell("\u8C03\u5EA6\u5F15\u64CE/\u5730\u56FE\u670D\u52A1/\u8BC4\u4F30\u670D\u52A1\u72EC\u7ACB\u90E8\u7F72 - \u76EE\u6807: \u6D77\u5EB7RCS\u67B6\u6784\u7EA7\u522B", 5917)
          ]}),
          new TableRow({ children: [
            createCell("2", 603, false, { align: AlignmentType.CENTER }),
            createCell("\u6D88\u606F\u961F\u5217\u96C6\u6210", 2506),
            createCell(" Kafka/RabbitMQ \u5F02\u6B65\u4EFB\u52A1\u5904\u7406 - \u884C\u4E1A\u6807\u51C6", 5917)
          ]}),
          new TableRow({ children: [
            createCell("3", 603, false, { align: AlignmentType.CENTER }),
            createCell(" GPU \u63A8\u7406\u52A0\u901F", 2506),
            createCell(" ONNX Runtime + TensorRT - \u76EE\u6807: RL\u63A8\u7406 <1ms", 5917)
          ]}),
          new TableRow({ children: [
            createCell("4", 603, false, { align: AlignmentType.CENTER }),
            createCell(" 3D \u53EF\u89C6\u5316\u5347\u7EA7", 2506),
            createCell(" Three.js/Babylon.js \u6570\u5B57\u5B5C\u751F - \u76EE\u6807: \u6781\u667A\u5609G-Studio\u7EA7\u522B", 5917)
          ]}),
          new TableRow({ children: [
            createCell("5", 603, false, { align: AlignmentType.CENTER }),
            createCell(" VDA 5050 \u534F\u8BAE\u652F\u6301", 2506),
            createCell(" \u7B2C\u4E09\u65B9AGV\u54C1\u724C\u9002\u914D - \u76EE\u6807: SEER\u5F02\u6784\u8C03\u5EA6\u80FD\u529B", 5917)
          ]}),
          new TableRow({ children: [
            createCell("6", 603, false, { align: AlignmentType.CENTER }),
            createCell(" RL\u6A21\u578B\u6301\u7EED\u5B66\u4E60", 2506),
            createCell(" \u5728\u7EBF Fine-tuning\u673A\u5236 - \u884C\u4E1A\u524D\u6CBF\u80FD\u529B", 5917)
          ]})
        ]
      }),

      createHeading2("9.3 \u957F\u671F\u613F\u666F (6-12\u4E2A\u6708)"),
      createBulletPoint("\u4E91\u539F\u751F: Kubernetes Operator + Helm Chart - \u81EA\u52A8\u6269\u7F29\u5BB9\u80FD"),
      createBulletPoint("\u6570\u5B57\u5B5C\u751F: Unity/Unreal\u6E32\u67D3 + \u7269\u7406\u4EFF\u771F - \u9AD8\u7AEF\u9879\u76EE\u7ADE\u4E89\u529B"),
      createBulletPoint("\u591A\u79DF\u6237 SaaS: \u79DF\u6237\u9694\u79BB + \u7528\u91CF\u8BA1\u8D39 - \u5546\u4E1A\u5316\u8DEF\u5F84"),
      createBulletPoint("\u884C\u4E1A\u89E3\u51B3\u65B9\u6848\u5305: \u7535\u5546/\u5236\u9020/\u533B\u836F\u9884\u914D\u7F6E - \u5FEB\u901F\u4EA4\u4ED8"),
      createBulletPoint("\u5F00\u653E\u7B97\u6CD5\u5E02\u573A: \u7B2C\u4E09\u65B9\u7B97\u6CD5\u63D2\u4EF6\u673A\u5236 - \u751F\u6001\u6784\u5EFA"),

      // ========== 第十章：结论与建议 ==========
      createHeading1("\u5341\u3001\u7ED3\u8BBA\u4E0E\u5EFA\u8BAE"),

      createHeading2("10.1 \u603B\u4F53\u8BC4\u4EF7"),
      createParagraph({ text: "AgvTms\u662F\u4E00\u4E2A\"\u7B97\u6CD5\u5BC6\u5EA6\u6781\u9AD8\"\u7684AGV\u8C03\u5EA6\u7CFB\u7EDF\u539F\u578B\u3002\u5176\u6838\u5FC3\u7ADE\u4E89\u529B\u4F53\u73B0\u5728:", bold: true }),
      createBulletPoint("\u7B97\u6CD5\u5E7F\u5EA6: 6\u79CD\u8C03\u5EA6\u7B97\u6CD5(\u5305\u62B9RL)\u5728\u884C\u4E1A\u4E2D\u7F55\u89C1"),
      createBulletPoint("\u7B97\u6CD5\u6DF1\u5EA6: MIP\u4E09\u5C42\u534F\u8C03 + \u62C9\u683C\u6717\u65E5\u677E\u5F1B\u8FBE\u5230\u7814\u7A76\u751F/\u535A\u58EB\u8BBA\u6587\u6C34\u5E73"),
      createBulletPoint("\u5B8C\u6574\u6027: \u5168\u94FE\u6761\u8FDE\u63A5 \u8BAD\u7EC3 \u2192 \u8BC4\u4F30 \u2192 \u90E8\u7F72 \u2192 \u76D1\u63A7"),
      createBulletPoint("\u72EC\u7279\u6027: \u8F93\u9001\u7EBF\u534F\u540C + NLP\u4F18\u5316\u662F\u660E\u663E\u7684\u5E02\u573A\u5DEE\u5F02\u5316"),

      createHeading2("10.2 \u6700\u7EC8\u8BC4\u5206"),
      new Table({
        width: { size: 7026, type: WidthType.DXA },
        columnWidths: [3500, 1763, 1763],
        rows: [
          new TableRow({ children: [
            createCell("\u8BC4\u4F30\u7EF4\u5EA6", 3500, true),
            createCell("\u5F97\u5206 (/10)", 1763, true),
            createCell("\u52A0\u6743\u5F97\u5206", 1763, true)
          ]}),
          new TableRow({ children: [
            createCell("\u7B97\u6CD5\u521B\u65B0\u6027", 3500),
            createCell("9.0", 1763, false, { align: AlignmentType.CENTER, shading: "D4EDDA" }),
            createCell("2.25", 1763, false, { align: AlignmentType.CENTER, shading: "D4EDDA" })
          ]}),
          new TableRow({ children: [
            createCell("\u529F\u80FD\u5B8C\u6574\u5EA6", 3500),
            createCell("6.5", 1763, false, { align: AlignmentType.CENTER }),
            createCell("1.30", 1763, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("\u5DE5\u7A0B\u8D28\u91CF", 3500),
            createCell("5.5", 1763, false, { align: AlignmentType.CENTER }),
            createCell("1.10", 1763, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("\u6613\u7528\u6027 / \u6587\u6863", 3500),
            createCell("5.0", 1763, false, { align: AlignmentType.CENTER }),
            createCell("0.75", 1763, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("\u5546\u4E1A\u5316\u6F5C\u529B", 3500),
            createCell("6.0", 1763, false, { align: AlignmentType.CENTER }),
            createCell("1.20", 1763, false, { align: AlignmentType.CENTER })
          ]}),
          new TableRow({ children: [
            createCell("\u603B\u5206", 3500, false, { shading: "2E75B6" }),
            createCell("-", 1763, false, { align: AlignmentType.CENTER, shading: "2E75B6" }),
            createCell("6.60 / 10", 1763, false, { align: AlignmentType.CENTER, shading: "E8F4BD", bold: true })
          ]})
        ]
      }),

      createParagraph({ text: "\u7ECD\u5B9A\u7B49\u7EA7: B+ (\u9AD8\u4E8E\u5E73\u5747\uFF0C\u5177\u5907\u7A81\u51FA\u4EAE\u70B9\u7684\u6210\u957F\u578B\u4EA7\u54C1)", bold: true, size: 26, color: "2E75B6" }),

      createHeading2("10.3 \u4E00\u53E5\u8BDD\u603B\u7ED3"),
      createParagraph({ 
        text: "AgvTms\u5728AGV\u8C03\u5EA6\u7B97\u6CD5\u9886\u57DF\u7684\u7814\u7A76\u6DF1\u5EA6\u548C\u521B\u65B0\u6027\u65B9\u9762\u5DF2\u8FBE\u5230\u884C\u4E1A\u524D\u5217\u6C34\u5E73\uFF0C\u7279\u522B\u662F\u5728RL\u5E94\u7528\u3001\u8F93\u9001\u7EBF\u534F\u540C\u3001\u591A\u5C42\u4F18\u5316\u7B49\u65B9\u9762\u5F62\u6210\u4E86\u72EC\u7279\u7684\u6280\u672F\u58C1\u5792\u3002\u82E5\u80FD\u8865\u9F50\u5DE5\u7A0B\u5316\u3001\u53EF\u89C6\u5316\u548C\u90E8\u7F72\u8FD0\u7EF4\u7B49\u65B9\u9762\u7684\u77ED\u677F\uFF0C\u5B8C\u5168\u6709\u6F5C\u529B\u6210\u4E3A\u4E2D\u5C0F\u4F01\u4E1A\u5E02\u573A\u548C\u5B66\u672F\u9886\u57DF\u7684\u9996\u9009AGV\u8C03\u5EA6\u89E3\u51B3\u65B9\u6848\u3002",
        italics: true,
        size: 24
      }),

      // ========== 分页 ==========
      new Paragraph({ children: [new PageBreak()] }),

      // ========== 附录 ==========
      createHeading1("\u9644\u5F55 A: \u53C2\u8003\u8D44\u6E90"),
      createBulletPoint("\u6781\u667A\u5609 Geek+: https://www.geekplus.com/en"),
      createBulletPoint("\u6D77\u5EB7\u673A\u5668\u4eba RCS: https://blog.csdn.net/HW18296412587/article/details/153929862"),
      createBulletPoint("\u6D87\u67D5\u521B\u65B0: https://www.hairobotics.cn"),
      createBulletPoint("\u4ED9\u5DE5\u667A\u80FD SEER: https://www.seer-robot.com"),
      createBulletPoint(" RCS\u6280\u672F\u67B6\u6784: https://jishuzhan.net/article/2040011738065145857"),
      createBulletPoint(" 2026\u5927\u5382\u5546\u6A2A\u8BC4: https://www.sohu.com/a/1015725815_122741106"),

      createHeading1("\u9644\u5F55 B: \u672F\u8BED\u8868"),
      new Table({
        width: { size: 9026, type: WidthType.DXA },
        columnWidths: [1804, 4013, 3209],
        rows: [
          new TableRow({ children: [
            createCell("\u672F\u8BED", 1804, true),
            createCell("\u82F1\u6587\u5168\u79F0", 4013, true),
            createCell("\u4E2D\u6587\u89E3\u91CA", 3209, true)
          ]}),
          new TableRow({ children: [ createCell("AGV", 1804), createCell("Automated Guided Vehicle", 4013), createCell("\u81EA\u52A8\u5F15\u5BFC\u8FD0\u8F93\u8F66", 3209) ]}),
          new TableRow({ children: [ createCell("AMR", 1804), createCell("Autonomous Mobile Robot", 4013), createCell("\u81EA\u4E3B\u79FB\u52A8\u673A\u5668\u4eba", 3209) ]}),
          new TableRow({ children: [ createCell("TMS", 1804), createCell("Transportation Management System", 4013), createCell("\u8FD0\u8F93\u7BA1\u7406\u7CFB\u7EDF", 3209) ]}),
          new TableRow({ children: [ createCell("RCS", 1804), createCell("Robot Control System", 4013), createCell("\u673A\u5668\u4eba\u63A7\u5236\u7CFB\u7EDF", 3209) ]}),
          new TableRow({ children: [ createCell("MIP", 1804), createCell("Mixed Integer Programming", 4013), createCell("\u6DF7\u5408\u6574\u6570\u89C4\u5212", 3209) ]}),
          new TableRow({ children: [ createCell("CP-SAT", 1804), createCell("Constraint Programming - Satisfiability", 4013), createCell("\u7EA6\u675F\u89C4\u5212\u6EE1\u8DB3\u95EE\u9898", 3209) ]}),
          new TableRow({ children: [ createCell("RL", 1804), createCell("Reinforcement Learning", 4013), createCell("\u5F3A\u5316\u5B66\u4E60", 3209) ]}),
          new TableRow({ children: [ createCell("DQN", 1804), createCell("Deep Q-Network", 4013), createCell("\u6DF1\u5EA6Q\u7F51\u7EDC", 3209) ]}),
          new TableRow({ children: [ createCell("PPO", 1804), createCell("Proximal Policy Optimization", 4013), createCell("\u8FD1\u7AEF\u7B56\u7565\u4F18\u5316", 3209) ]}),
          new TableRow({ children: [ createCell("ACO", 1804), createCell("Ant Colony Optimization", 4013), createCell("\u8681\u7FA4\u4F18\u5316\u7B97\u6CD5", 3209) ]}),
          new TableRow({ children: [ createCell("SA", 1804), createCell("Simulated Annealing", 4013), createCell("\u6A21\u62DF\u9000\u706B\u7B97\u6CD5", 3209) ]}),
          new TableRow({ children: [ createCell("SIPP", 1804), createCell("Safe Interval Path Planning", 4013), createCell("\u5B89\u5168\u95F4\u9694\u8DEF\u5F84\u89C4\u5212", 3209) ]}),
          new TableRow({ children: [ createCell("SLAM", 1804), createCell("Simultaneous Localization and Mapping", 4013), createCell("\u540C\u6B65\u5B9A\u4F4E\u4E0E\u5EFA\u56FE", 3209) ]})
        ]
      }),

      createHeading1("\u9644\u5F55 C: \u7248\u672C\u4FE1\u606F"),
      new Table({
        width: { size: 7026, type: WidthType.DXA },
        columnWidths: [2500, 4526],
        rows: [
          new TableRow({ children: [ createCell("\u62A5\u544A\u7248\u672C", 2500, true), createCell("v1.0", 4526) ]}),
          new TableRow({ children: [ createCell("\u751F\u6210\u65E5\u671F", 2500, true), createCell("2026-06-15", 4526) ]}),
          new TableRow({ children: [ createCell("\u5206\u6790\u5BF9\u8C61", 2500, true), createCell("AgvTms main branch (\u6700\u65B0\u4EE3\u7801)", 4526) ]}),
          new TableRow({ children: [ createCell("\u5BF9\u6BD4\u57FA\u51C6", 2500, true), createCell("2025-2026\u4E3B\u6D41AGV\u8C03\u5EA6\u4EA7\u54C1\u516C\u5F00\u4FE1\u606F", 4526) ]}),
          new TableRow({ children: [ createCell("\u58F0\u660E", 2500, true), createCell("\u5546\u4E1c\u4EA7\u54C1\u53C2\u6570\u6765\u6E90\u4E8E\u516C\u5F00\u8D44\u6599\uFF1B\u4EC5\u4F9B\u7814\u7A76\u53C2\u8003", 4526) ]})
        ]
      }),

      new Paragraph({ spacing: { before: 400 } }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "--- \u62A5\u544A\u5B8C\u7ED3 ---", italics: true, size: 20, color: "999999", font: "\u5B8B\u4F53" })]
      })
    ]
  }]
});

// 生成文档
Packer.toBuffer(doc).then(buffer => {
  const outputPath = '/Users/water/Documents/AgvTms/docs/PRODUCT_COMPARISON_REPORT_CN.docx';
  fs.writeFileSync(outputPath, buffer);
  console.log(`\u6587\u6863\u5DF2\u4FDD\u5B58: ${outputPath}`);
  console.log(`\u6587\u4EF6\u5927\u5C0F: ${(buffer.length / 1024).toFixed(2)} KB`);
});
