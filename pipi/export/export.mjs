import fs from "node:fs/promises";
import pptxgen from "pptxgenjs";

// The service builds this manifest from validated, owner-scoped database data.
// Images are explicit local files; URL fetching and HTML evaluation are absent.
const [manifestFile, outputFile] = process.argv.slice(2);
const { title, slides, assets } = JSON.parse(
  await fs.readFile(manifestFile, "utf8"),
);
if (!Array.isArray(slides) || slides.length < 1 || slides.length > 30)
  throw new Error("Invalid slide count");
const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "Pipi PPT / Presenton";
pptx.subject = "Generated using pipiapi";
pptx.title = title;
pptx.lang = "zh-CN";
pptx.theme = {
  headFontFace: "Noto Sans CJK SC",
  bodyFontFace: "Noto Sans CJK SC",
  lang: "zh-CN",
};
const hex = (color) => color.replace("#", "");
for (const page of slides) {
  const slide = pptx.addSlide();
  slide.background = { color: hex(page.background) };
  slide.addNotes(page.notes || "");
  for (const element of page.elements) {
    const imageKey = element.asset_id || element.builtin_asset;
    const box = {
      x: element.x / 96,
      y: element.y / 96,
      w: element.w / 96,
      h: element.h / 96,
    };
    const style = {
      ...box,
      fontFace: element.font,
      fontSize: element.font_size * 0.75,
      color: hex(element.color),
      bold: element.bold,
      margin: 0,
      breakLine: false,
    };
    if (element.type === "text")
      slide.addText(element.text, {
        ...style,
        align: element.align || "left",
        valign: element.valign || "top",
        lineSpacingMultiple: element.line_height || 1.25,
        fit: "shrink",
        paraSpaceAfterPt: 0,
      });
    else if (element.type === "shape")
      slide.addShape(pptx.ShapeType[element.shape] || pptx.ShapeType.rect, {
        ...box,
        fill: { color: hex(element.fill) },
        line: { color: hex(element.fill), transparency: 100 },
      });
    else if (element.type === "image" && imageKey && assets[imageKey]) {
      slide.addImage({
        path: assets[imageKey],
        ...box,
        ...(element.image_fit === "fill"
          ? {}
          : {
              sizing: {
                type: element.image_fit || "contain",
                w: box.w,
                h: box.h,
              },
            }),
      });
    } else if (element.type === "table" && element.rows.length) {
      slide.addTable(element.rows, {
        ...style,
        border: { type: "solid", color: "DDE3EB", pt: 1 },
        fill: "FFFFFF",
        autoPage: false,
        rowH: box.h / element.rows.length,
        margin: 3,
      });
    } else if (element.type === "chart" && element.labels.length) {
      slide.addChart(
        pptx.ChartType.bar,
        [{ name: page.name, labels: element.labels, values: element.values }],
        {
          ...box,
          showLegend: false,
          showValue: true,
          catAxisLabelFontFace: element.font,
          valAxisLabelFontFace: element.font,
          chartColors: [hex(element.fill)],
          showTitle: false,
        },
      );
    }
  }
}
await pptx.writeFile({ fileName: outputFile });
