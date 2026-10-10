"use client";
import { useState } from "react";
import { api, blankElement, Deck, Element, Slide } from "./types";
import { Scene } from "./scene";

export function Editor({
  deck,
  update,
  run,
  save,
  busy,
  t,
}: {
  deck: Deck;
  update: (deck: Deck) => void;
  run: (path: string, body?: unknown) => void;
  save: () => Promise<void>;
  busy: boolean;
  t: (zh: string, en: string) => string;
}) {
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState("");
  const [instruction, setInstruction] = useState("");
  const slide = deck.slides[Math.min(page, deck.slides.length - 1)];
  const element = slide?.elements.find((e) => e.id === selected);
  function changeSlide(next: Slide) {
    update({
      ...deck,
      slides: deck.slides.map((s) => (s.id === slide.id ? next : s)),
    });
  }
  function changeElement(changes: Partial<Element>) {
    changeSlide({
      ...slide,
      elements: slide.elements.map((e) =>
        e.id === selected ? { ...e, ...changes } : e,
      ),
    });
  }
  function move(direction: number) {
    const target = page + direction;
    if (target < 0 || target >= deck.slides.length) return;
    const slides = [...deck.slides],
      outline = [...deck.outline];
    [slides[page], slides[target]] = [slides[target], slides[page]];
    [outline[page], outline[target]] = [outline[target], outline[page]];
    update({ ...deck, slides, outline });
    setPage(target);
  }
  async function replace(file: File) {
    const form = new FormData();
    form.append("file", file);
    const result = await api<{ id: string }>("/assets?purpose=image", {
      method: "POST",
      body: form,
    });
    changeElement({ asset_id: result.id, builtin_asset: null });
  }
  return (
    <fieldset className="editor" disabled={busy}>
      <div className="editor-top">
        <input
          className="deck-title"
          aria-label={t("作品名称", "Title")}
          value={deck.title}
          onChange={(e) => update({ ...deck, title: e.target.value })}
        />
        <button disabled={busy} onClick={() => void save()}>
          {t("保存", "Save")}
        </button>
        <button disabled={busy} onClick={() => run(`/decks/${deck.id}/export`)}>
          {t("导出 PPTX", "Export PPTX")}
        </button>
        <button
          disabled={busy}
          onClick={() => run(`/decks/${deck.id}/export?format=pdf`)}
        >
          PDF
        </button>
      </div>
      <ResearchSummary deck={deck} t={t} />
      {!deck.slides.length ? (
        <section className="outline">
          <p className="eyebrow">02 / {t("确认内容", "OUTLINE")}</p>
          <h2>{t("先把故事讲清楚", "Shape the story first")}</h2>
          {deck.outline.length ? (
            <>
              <p className="muted">
                {t(
                  "调整每页标题和要点，然后开始生成。",
                  "Edit each slide’s title and key points before generating.",
                )}
              </p>
              {deck.outline.map((text, i) => (
                <label key={i} className="outline-row">
                  <span>{String(i + 1).padStart(2, "0")}</span>
                  <textarea
                    aria-label={`${t("大纲", "Outline")} ${i + 1}`}
                    value={text}
                    maxLength={1000}
                    onChange={(e) =>
                      update({
                        ...deck,
                        outline: deck.outline.map((item, j) =>
                          i === j ? e.target.value : item,
                        ),
                      })
                    }
                  />
                </label>
              ))}
              <button
                className="primary"
                disabled={busy}
                onClick={() => run(`/decks/${deck.id}/generate`)}
              >
                {t("确认大纲并生成", "Confirm & generate")}
              </button>
            </>
          ) : (
            <p role="status">
              {t(
                "正在生成大纲，可以离开页面，进度会自动保存。",
                "Your outline is being generated. Progress is saved automatically.",
              )}
            </p>
          )}
        </section>
      ) : (
        <div className="editor-body">
          <aside className="slide-list">
            {deck.slides.map((s, i) => (
              <button
                className={i === page ? "selected" : ""}
                key={s.id}
                onClick={() => {
                  setPage(i);
                  setSelected("");
                }}
              >
                <Scene slide={s} />
                <span>{String(i + 1).padStart(2, "0")}</span>
              </button>
            ))}
            <button
              disabled={deck.slides.length >= 30 || busy}
              onClick={() => {
                const id = crypto.randomUUID();
                update({
                  ...deck,
                  slides: [
                    ...deck.slides,
                    {
                      id,
                      name: t("新页面", "New slide"),
                      background: "#FFFFFF",
                      elements: [blankElement("text")],
                      notes: "",
                    },
                  ],
                  outline: [...deck.outline, t("新页面", "New slide")],
                });
                setPage(deck.slides.length);
              }}
            >
              ＋ {t("新增页", "Add slide")}
            </button>
          </aside>
          <section className="canvas-area">
            <div className="canvas-tools">
              <span>
                {page + 1} / {deck.slides.length}
              </span>
              <button onClick={() => move(-1)} disabled={page === 0 || busy}>
                {t("上移", "Move up")}
              </button>
              <button
                onClick={() => move(1)}
                disabled={page >= deck.slides.length - 1 || busy}
              >
                {t("下移", "Move down")}
              </button>
              <button
                disabled={deck.slides.length <= 1 || busy}
                onClick={() => {
                  update({
                    ...deck,
                    slides: deck.slides.filter((s) => s.id !== slide.id),
                    outline: deck.outline.filter((_, i) => i !== page),
                  });
                  setPage(Math.max(0, page - 1));
                }}
              >
                {t("删除页", "Delete slide")}
              </button>
            </div>
            <Scene slide={slide} selected={selected} onSelect={setSelected} />
            <div className="insert-tools">
              {(["text", "image", "table", "chart", "shape"] as const).map(
                (type, i) => (
                  <button
                    key={type}
                    disabled={busy}
                    onClick={() => {
                      const el = blankElement(type);
                      changeSlide({
                        ...slide,
                        elements: [...slide.elements, el],
                      });
                      setSelected(el.id);
                    }}
                  >
                    ＋ {t(["文字", "图片", "表格", "图表", "形状"][i], type)}
                  </button>
                ),
              )}
            </div>
            <label>
              {t("演讲备注", "Speaker notes")}
              <textarea
                value={slide.notes}
                onChange={(e) =>
                  changeSlide({ ...slide, notes: e.target.value })
                }
              />
            </label>
            <div className="rewrite">
              <input
                aria-label={t("AI 修改要求", "AI rewrite instruction")}
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder={t(
                  "例如：让这一页更简洁，突出三个重点",
                  "e.g. Make this slide concise with three key points",
                )}
              />
              <button
                disabled={busy || !instruction.trim()}
                onClick={() =>
                  run(`/decks/${deck.id}/rewrite`, { index: page, instruction })
                }
              >
                {t("AI 重写本页", "Rewrite slide")}
              </button>
            </div>
            <p className="muted small">
              {t(
                "AI 重写会产生模型费用；手动编辑和导出不调用模型。",
                "AI rewrites incur model charges. Manual edits and exports do not call models.",
              )}
            </p>
          </section>
          <aside className="properties">
            <h3>{t("页面元素", "Slide elements")}</h3>
            {!element ? (
              <p className="muted">
                {t(
                  "点击页面中的元素开始编辑。",
                  "Select an element on the slide to edit.",
                )}
              </p>
            ) : (
              <>
                <p className="eyebrow">{element.type}</p>
                {element.type === "text" && (
                  <>
                    <textarea
                      aria-label={t("文字内容", "Text content")}
                      value={element.text}
                      maxLength={6000}
                      onChange={(e) => changeElement({ text: e.target.value })}
                    />
                    <label>
                      {t("字号", "Font size")}
                      <input
                        type="number"
                        min={8}
                        max={150}
                        value={element.font_size}
                        onChange={(e) =>
                          changeElement({ font_size: Number(e.target.value) })
                        }
                      />
                    </label>
                    <label>
                      {t("文字颜色", "Text color")}
                      <input
                        type="color"
                        value={element.color}
                        onChange={(e) =>
                          changeElement({ color: e.target.value })
                        }
                      />
                    </label>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={element.bold}
                        onChange={(e) =>
                          changeElement({ bold: e.target.checked })
                        }
                      />
                      {t("粗体", "Bold")}
                    </label>
                  </>
                )}
                {element.type === "image" && (
                  <label className="upload-button">
                    {t("替换图片", "Replace image")}
                    <input
                      type="file"
                      accept=".png,.jpg,.jpeg,.webp"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file)
                          void replace(file).catch((error) =>
                            alert(error.message),
                          );
                      }}
                    />
                  </label>
                )}
                {element.type === "table" && (
                  <div className="data-editor">
                    {element.rows.map((row, i) => (
                      <div key={i}>
                        {row.map((cell, j) => (
                          <input
                            aria-label={`${i + 1},${j + 1}`}
                            key={j}
                            value={cell}
                            onChange={(e) =>
                              changeElement({
                                rows: element.rows.map((r, k) =>
                                  k === i
                                    ? r.map((v, l) =>
                                        l === j ? e.target.value : v,
                                      )
                                    : r,
                                ),
                              })
                            }
                          />
                        ))}
                      </div>
                    ))}
                    <button
                      onClick={() =>
                        changeElement({
                          rows: [
                            ...element.rows,
                            (element.rows[0] || ["", ""]).map(() => ""),
                          ],
                        })
                      }
                      disabled={element.rows.length >= 25}
                    >
                      ＋ {t("行", "Row")}
                    </button>
                  </div>
                )}
                {element.type === "chart" && (
                  <div className="data-editor">
                    {element.labels.map((label, i) => (
                      <div key={i}>
                        <input
                          aria-label={t("分类", "Category")}
                          value={label}
                          onChange={(e) =>
                            changeElement({
                              labels: element.labels.map((v, j) =>
                                j === i ? e.target.value : v,
                              ),
                            })
                          }
                        />
                        <input
                          aria-label={t("数值", "Value")}
                          type="number"
                          value={element.values[i]}
                          onChange={(e) =>
                            changeElement({
                              values: element.values.map((v, j) =>
                                j === i ? Number(e.target.value) : v,
                              ),
                            })
                          }
                        />
                      </div>
                    ))}
                    <button
                      disabled={element.labels.length >= 30}
                      onClick={() =>
                        changeElement({
                          labels: [...element.labels, t("新分类", "Category")],
                          values: [...element.values, 0],
                        })
                      }
                    >
                      ＋ {t("分类", "Category")}
                    </button>
                    {element.labels.length > 0 && (
                      <button
                        onClick={() =>
                          changeElement({
                            labels: element.labels.slice(0, -1),
                            values: element.values.slice(0, -1),
                          })
                        }
                      >
                        {t("删除末项", "Remove last item")}
                      </button>
                    )}
                  </div>
                )}
                {["shape", "chart"].includes(element.type) && (
                  <label>
                    {t("填充颜色", "Fill")}
                    <input
                      type="color"
                      value={element.fill}
                      onChange={(e) => changeElement({ fill: e.target.value })}
                    />
                  </label>
                )}
                <div className="position-grid">
                  {(["x", "y", "w", "h"] as const).map((key) => (
                    <label key={key}>
                      {key.toUpperCase()}
                      <input
                        type="number"
                        min={0}
                        max={key === "x" || key === "w" ? 1280 : 720}
                        value={Math.round(element[key])}
                        onChange={(e) =>
                          changeElement({ [key]: Number(e.target.value) })
                        }
                      />
                    </label>
                  ))}
                </div>
                <button
                  className="danger"
                  onClick={() =>
                    changeSlide({
                      ...slide,
                      elements: slide.elements.filter((e) => e.id !== selected),
                    })
                  }
                >
                  {t("删除元素", "Delete element")}
                </button>
              </>
            )}
          </aside>
        </div>
      )}
    </fieldset>
  );
}

function ResearchSummary({
  deck,
  t,
}: {
  deck: Deck;
  t: (zh: string, en: string) => string;
}) {
  if (!deck.research || deck.research.mode === "off") return null;
  return (
    <section className="research-card" aria-live="polite">
      <strong>
        {deck.research.status === "complete"
          ? t("已补充外部资料", "External research added")
          : deck.research.status === "pending"
            ? t("正在检查资料并搜索", "Checking sources and searching")
            : t("资料检索状态", "Research status")}
      </strong>
      {deck.research.warning && (
        <p className="small muted">
          {t(
            "联网资料暂不可用，已保留当前资料并标记风险。",
            "External research was unavailable. Existing material was kept and marked.",
          )}
        </p>
      )}
      {!!deck.research.sources?.length && (
        <div className="research-sources">
          {deck.research.sources.map((source) => (
            <a key={source.id} href={source.url} target="_blank" rel="noreferrer">
              {source.title}
            </a>
          ))}
        </div>
      )}
    </section>
  );
}
