"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import {
  ArrowUpRight,
  Clock3,
  FileStack,
  LayoutTemplate,
  Plus,
  Settings2,
  Sparkles,
  Presentation,
  LogOut,
  X,
  Check,
} from "lucide-react";
import {
  api,
  AdminConfig,
  Deck,
  Job,
  submitJob,
  Policy,
  Profile,
  ReasoningEffort,
  Template,
  TemplateDetail,
} from "./types";
import { Editor } from "./editor";
import { Scene } from "./scene";

type Work = { id: string; title: string; updated: string; pages: number };
type View = "create" | "works" | "templates" | "jobs" | "admin" | "files";
const active = (job: Job) => ["queued", "running"].includes(job.status);
const loadWorkspace = () =>
  Promise.all([
    api<Profile>("/me"),
    api<Template[]>("/templates"),
    api<Work[]>("/decks"),
    api<Job[]>("/jobs"),
  ]);

export default function Home() {
  const [language, setLanguage] = useState<"zh" | "en">("zh");
  const t = useCallback(
    (zh: string, en: string) => (language === "zh" ? zh : en),
    [language],
  );
  const [profile, setProfile] = useState<Profile | null | undefined>(undefined);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [works, setWorks] = useState<Work[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [view, setView] = useState<View>("create");
  const [deck, setDeck] = useState<Deck | null>(null);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [topic, setTopic] = useState("");
  const [count, setCount] = useState(10);
  const [templateID, setTemplateID] = useState("executive");
  const [textModel, setTextModel] = useState("");
  const [imageModel, setImageModel] = useState("");
  const [images, setImages] = useState(true);
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>("auto");
  const [documents, setDocuments] = useState<{ id: string; name: string }[]>(
    [],
  );
  const [preview, setPreview] = useState<{
    id: string;
    data: TemplateDetail;
    confirmed: boolean;
    builtin: boolean;
  } | null>(null);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [adminConfig, setAdminConfig] = useState<AdminConfig | null>(null);
  const [files, setFiles] = useState<
    { id: string; name: string; size: number; purpose: string }[]
  >([]);

  const applyWorkspace = useCallback(
    ([nextProfile, nextTemplates, nextWorks, nextJobs]: Awaited<
      ReturnType<typeof loadWorkspace>
    >) => {
      setProfile(nextProfile);
      setTemplates(nextTemplates);
      setWorks(nextWorks);
      setJobs(nextJobs);
      setTextModel((current) => current || nextProfile.models.text[0] || "");
      setImageModel((current) =>
        nextProfile.models.image.includes(current)
          ? current
          : nextProfile.models.image[0] || "",
      );
      setImages((current) => current && nextProfile.models.image.length > 0);
    },
    [],
  );
  const refresh = useCallback(async () => {
    applyWorkspace(await loadWorkspace());
  }, [applyWorkspace]);
  useEffect(() => {
    void loadWorkspace()
      .then(applyWorkspace)
      .catch((e) => {
        setProfile(null);
        if (
          ![
            "login_required",
            "authorization_expired",
            "request_failed_401",
          ].includes(e.message)
        )
          setError(e.message);
      });
  }, [applyWorkspace]);
  const hasProfile = Boolean(profile);
  const deckId = deck?.id;
  useEffect(() => {
    if (!hasProfile) return;
    let cancelled = false;
    const timer = setInterval(() => {
      void Promise.all([api<Job[]>("/jobs"), api<Work[]>("/decks")])
        .then(([nextJobs, nextWorks]) => {
          setJobs(nextJobs);
          setWorks(nextWorks);
        })
        .catch((e) => setError(e.message));
      if (deckId && !dirtyRef.current && !busyRef.current)
        void api<Deck>("/decks/" + deckId)
          .then((next) => {
            if (!cancelled && !dirtyRef.current && !busyRef.current)
              setDeck((current) =>
                current?.id === deckId && current.version <= next.version
                  ? next
                  : current,
              );
          })
          .catch((e) => setError(e.message));
    }, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [hasProfile, deckId]); // Polling must not overwrite unsaved editor changes.
  useEffect(() => {
    function warn(e: BeforeUnloadEvent) {
      if (dirtyRef.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  function update(next: Deck) {
    setDeck(next);
    setDirty(true);
    dirtyRef.current = true;
  }
  async function save(): Promise<Deck> {
    if (!deck) throw new Error("deck_not_found");
    if (!dirtyRef.current) return deck;
    const result = await api<Deck>("/decks/" + deck.id, {
      method: "PUT",
      body: JSON.stringify({
        title: deck.title,
        outline: deck.outline,
        slides: deck.slides,
        version: deck.version,
      }),
    });
    setDeck(result);
    setDirty(false);
    dirtyRef.current = false;
    return result;
  }
  async function perform(work: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "request_failed");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function openDeck(id: string) {
    await perform(async () => {
      if (dirtyRef.current) await save();
      setDeck(await api<Deck>("/decks/" + id));
      setDirty(false);
      dirtyRef.current = false;
    });
  }
  async function navigate(next: View) {
    await perform(async () => {
      if (dirtyRef.current) await save();
      setDeck(null);
      setView(next);
      if (next === "admin") {
        const config = await api<AdminConfig>("/admin");
        setPolicy(config.policy);
        setAdminConfig(config);
      }
      if (next === "files") setFiles(await api("/assets"));
      await refresh();
    });
  }
  function run(path: string, extra?: unknown) {
    void perform(async () => {
      if (dirtyRef.current) await save();
      const body =
        path.includes("/generate") || path.includes("/rewrite")
          ? {
              text_model: textModel,
              image_model: imageModel,
              images,
              reasoning_effort: reasoningEffort,
              ...((extra as object) || {}),
            }
          : extra;
      await submitJob(path, body);
      await refresh();
    });
  }
  function create() {
    void perform(async () => {
      const job = await submitJob("/decks", {
        title: topic.trim().slice(0, 80),
        topic,
        template_id: templateID,
        slide_count: count,
        text_model: textModel,
        image_model: imageModel,
        images,
        reasoning_effort: reasoningEffort,
        document_ids: documents.map((d) => d.id),
      });
      if (job.deck_id) setDeck(await api<Deck>("/decks/" + job.deck_id));
      await refresh();
    });
  }
  function upload(file: File, purpose: string) {
    void perform(async () => {
      const form = new FormData();
      form.append("file", file);
      const asset = await api<{ id: string; name: string }>(
        "/assets?purpose=" + purpose,
        { method: "POST", body: form },
      );
      if (purpose === "template") {
        await submitJob("/templates/import", {
          asset_id: asset.id,
          text_model: textModel,
          reasoning_effort: reasoningEffort,
        });
        setView("jobs");
      } else setDocuments((items) => [...items, asset]);
      await refresh();
    });
  }
  async function openTemplate(template: Template) {
    await perform(async () => {
      setPreview({
        id: template.id,
        data: await api<TemplateDetail>("/templates/" + template.id),
        confirmed: template.confirmed,
        builtin: template.builtin,
      });
      setPreviewIndex(0);
    });
  }
  const working =
    !!deck &&
    jobs.some(
      (job) => job.deck_id === deck.id && active(job) && job.kind !== "export",
    );
  const nav = [
    { id: "create", zh: "创作台", en: "Create", icon: Sparkles },
    { id: "works", zh: "我的作品", en: "My work", icon: FileStack },
    { id: "templates", zh: "模板中心", en: "Templates", icon: LayoutTemplate },
    { id: "jobs", zh: "生成任务", en: "Activity", icon: Clock3 },
    { id: "files", zh: "文件空间", en: "Storage", icon: Presentation },
  ] as const;

  if (profile === undefined)
    return (
      <main className="loading">
        <Sparkles />
        <p>{t("正在载入工作台…", "Loading your workspace…")}</p>
      </main>
    );
  if (!profile)
    return (
      <main className="landing">
        <div className="brand">
          <span className="brand-mark">P</span>Pipi PPT
        </div>
        <button
          className="language"
          onClick={() => setLanguage(language === "zh" ? "en" : "zh")}
        >
          {language === "zh" ? "EN" : "中文"}
        </button>
        <div className="landing-copy">
          <span className="eyebrow">IDEAS → PRESENTATIONS</span>
          <h1>
            {t("让好想法，", "Give your ideas")}
            <br />
            <em>{t("有一个好开场。", "a brilliant beginning.")}</em>
          </h1>
          <p>
            {t(
              "从一句话到一份完整演示。选择模板，梳理内容，把更多时间留给真正重要的表达。",
              "From a thought to a complete presentation. Pick a template, shape your story, and focus on what matters.",
            )}
          </p>
          <a className="primary login-link" href="/api/auth/start">
            {t("使用 pipiapi 登录", "Continue with pipiapi")}
            <ArrowUpRight size={18} />
          </a>
          <p className="small muted">
            {t(
              "沿用你的余额与套餐 · 可编辑 PPTX · 内置与自定义模板",
              "Your balance & subscription · Editable PPTX · Custom templates",
            )}
          </p>
          {error && <p role="alert">{error}</p>}
        </div>
        <div className="landing-deck">
          <span>01 / THE NEXT CHAPTER</span>
          <h2>
            {t("每一次表达，", "Every story")}
            <br />
            {t("都值得被看见。", "deserves a stage.")}
          </h2>
          <div className="orb" />
          <footer>YOUR IDEAS. BEAUTIFULLY PRESENTED.</footer>
        </div>
        <p className="attribution">Built on Presenton · Apache-2.0</p>
      </main>
    );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link href="/" className="brand">
          <span className="brand-mark">P</span>Pipi PPT
        </Link>
        <div className="workspace-label">PERSONAL WORKSPACE</div>
        <nav>
          {nav.map((item) => (
            <button
              key={item.id}
              disabled={busy}
              className={!deck && view === item.id ? "current" : ""}
              onClick={() => void navigate(item.id)}
            >
              <item.icon size={19} />
              {t(item.zh, item.en)}
              {item.id === "jobs" && jobs.some(active) && (
                <i>{jobs.filter(active).length}</i>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          {profile.profile.role >= 10 && (
            <button disabled={busy} onClick={() => void navigate("admin")}>
              <Settings2 size={18} />
              {t("站点管理", "Administration")}
            </button>
          )}
          <div className="account">
            <span className="avatar">
              {(profile.profile.display_name || profile.profile.username).slice(
                0,
                1,
              )}
            </span>
            <div>
              <strong>
                {profile.profile.display_name || profile.profile.username}
              </strong>
              <small>pipiapi</small>
            </div>
            <button
              disabled={busy}
              aria-label={t("退出", "Sign out")}
              onClick={() =>
                void perform(async () => {
                  await api("/auth/logout", { method: "POST" });
                  location.assign("/");
                })
              }
            >
              <LogOut size={16} />
            </button>
          </div>
          <a
            className="attribution"
            href="https://github.com/presenton/presenton"
            target="_blank"
            rel="noreferrer"
          >
            Based on Presenton ↗
          </a>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div>
            <span className="breadcrumb">WORKSPACE / </span>
            {deck
              ? t("演示编辑", "Editor")
              : t(
                  nav.find((n) => n.id === view)?.zh || "站点管理",
                  nav.find((n) => n.id === view)?.en || "Administration",
                )}
            {dirty && (
              <span className="unsaved"> · {t("未保存", "Unsaved")}</span>
            )}
          </div>
          <div className="topbar-right">
            <span className="small muted">
              {t("费用由 pipiapi 结算", "Billed through pipiapi")}
            </span>
            <button
              onClick={() => setLanguage(language === "zh" ? "en" : "zh")}
            >
              {language === "zh" ? "EN" : "中文"}
            </button>
          </div>
        </header>
        {error && (
          <div className="error" role="alert">
            <span>{errorText(error, t)}</span>
            <button
              aria-label={t("关闭", "Dismiss")}
              onClick={() => setError("")}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {!profile.enabled && (
          <div className="notice">
            {t(
              "新建任务暂时关闭。你的作品仍然保留，可继续查看和导出。",
              "New generation is paused. Your work remains available to view and export.",
            )}
          </div>
        )}
        {deck ? (
          <>
            <div className="editor-status">
              {working && <span className="status-dot" />}
              {working
                ? t(
                    "生成中 · 已完成页面会逐页保存",
                    "Generating · Each completed slide is saved",
                  )
                : t(
                    "手动编辑无需模型消费",
                    "Manual editing does not incur model charges",
                  )}
            </div>
            <Editor
              key={deck.id}
              deck={deck}
              update={update}
              run={run}
              save={() =>
                perform(async () => {
                  await save();
                })
              }
              busy={busy || working}
              t={t}
            />
          </>
        ) : (
          <div className="content">
            {view === "create" && (
              <>
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">
                      A LITTLE IDEA. A GREAT PRESENTATION.
                    </p>
                    <h1>
                      {t(
                        "今天，想讲一个什么故事？",
                        "What story will you tell today?",
                      )}
                    </h1>
                    <p className="muted">
                      {t(
                        "给出主题或资料，让 AI 帮你搭好框架。",
                        "Start with a topic or source material. AI will help you shape the structure.",
                      )}
                    </p>
                  </div>
                  <span className="edition">PIPI / STUDIO 01</span>
                </div>
                <section className="composer">
                  <label className="sr-only" htmlFor="topic">
                    {t("演示主题", "Presentation topic")}
                  </label>
                  <textarea
                    id="topic"
                    maxLength={20000}
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    placeholder={t(
                      "例如：为一家新能源公司制作 2026 年度战略规划，面向管理层，突出市场机会与行动计划…",
                      "e.g. Create an annual strategy presentation for a renewable energy company, highlighting opportunities and an action plan…",
                    )}
                  />
                  <div className="attachments">
                    {documents.map((d) => (
                      <span key={d.id}>
                        {d.name}
                        <button
                          aria-label={t("移除资料", "Remove attachment")}
                          onClick={() =>
                            setDocuments((items) =>
                              items.filter((item) => item.id !== d.id),
                            )
                          }
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                  <div className="composer-footer">
                    <label className="upload-button">
                      <Plus size={16} />
                      {t("添加资料", "Add sources")}
                      <input
                        type="file"
                        accept=".pdf,.docx,.md,.txt"
                        disabled={busy || documents.length >= 5}
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (file) upload(file, "document");
                          e.target.value = "";
                        }}
                      />
                    </label>
                    <span className="small muted">
                      PDF / DOCX / MD / TXT · 20 MB
                    </span>
                    <label className="count-label">
                      {t("页数", "Slides")}
                      <input
                        type="number"
                        min={5}
                        max={30}
                        value={count}
                        onChange={(e) => setCount(Number(e.target.value))}
                      />
                    </label>
                  </div>
                </section>
                <section className="model-bar">
                  <label>
                    {t("文字与视觉模型", "Text & vision")}
                    <select
                      value={textModel}
                      onChange={(e) => setTextModel(e.target.value)}
                    >
                      {!profile.models.text.length && (
                        <option value="">
                          {t("暂无可用模型", "No available models")}
                        </option>
                      )}
                      {profile.models.text.map((m) => (
                        <option key={m}>{m}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    {t("图片模型", "Image model")}
                    <select
                      value={imageModel}
                      disabled={!images}
                      onChange={(e) => setImageModel(e.target.value)}
                    >
                      {profile.models.image.map((m) => (
                        <option key={m}>{m}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    {t("思考强度", "Reasoning")}
                    <select
                      value={reasoningEffort}
                      onChange={(e) =>
                        setReasoningEffort(e.target.value as ReasoningEffort)
                      }
                    >
                      <option value="auto">
                        {t("自动（Kimi K3 高思考）", "Auto (high for Kimi K3)")}
                      </option>
                      <option value="off">
                        {t("关闭（模型支持时）", "Off when supported")}
                      </option>
                      <option value="low">{t("低", "Low")}</option>
                      <option value="medium">{t("中", "Medium")}</option>
                      <option value="high">{t("高", "High")}</option>
                    </select>
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={images}
                      disabled={busy || profile.models.image.length === 0}
                      onChange={(e) => setImages(e.target.checked)}
                    />
                    {t("AI 配图", "AI images")}
                  </label>
                  {profile.pricing_url && (
                    <a
                      href={profile.pricing_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t("模型价格 ↗", "Model pricing ↗")}
                    </a>
                  )}
                </section>
                <div className="section-heading">
                  <h2>{t("给内容选一件合适的外衣", "Find the right look")}</h2>
                  <button onClick={() => void navigate("templates")}>
                    {t("管理模板", "Manage templates")}{" "}
                    <ArrowUpRight size={15} />
                  </button>
                </div>
                <TemplateGrid
                  templates={templates.filter((item) => item.confirmed)}
                  chosen={templateID}
                  choose={(id) => setTemplateID(id)}
                  preview={(item) => void openTemplate(item)}
                  t={t}
                />
                <div className="generate-footer">
                  <p>
                    {t(
                      "默认 16:9 · Kimi K3 自动使用高思考 · 渠道不支持时使用默认策略 · 高思考可能更耗额度",
                      "16:9 · Kimi K3 uses high reasoning in Auto · Unsupported channels use their default · Higher reasoning may cost more",
                    )}
                  </p>
                  <button
                    className="primary"
                    disabled={
                      busy ||
                      !profile.enabled ||
                      !topic.trim() ||
                      !textModel ||
                      (images && !imageModel) ||
                      count < 5 ||
                      count > 30
                    }
                    onClick={create}
                  >
                    <Sparkles size={18} />
                    {t("生成演示大纲", "Generate outline")}
                  </button>
                </div>
              </>
            )}
            {view === "templates" && (
              <>
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">YOUR VISUAL LIBRARY</p>
                    <h1>{t("模板中心", "Template library")}</h1>
                    <p className="muted">
                      {t(
                        "延续品牌风格，也让每次表达有所不同。",
                        "Keep your brand consistent and every presentation fresh.",
                      )}
                    </p>
                  </div>
                  <label className="primary upload-button">
                    {t("上传 PPTX 模板", "Upload PPTX template")}
                    <input
                      type="file"
                      accept=".pptx"
                      disabled={busy || !textModel}
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) upload(file, "template");
                        e.target.value = "";
                      }}
                    />
                  </label>
                </div>
                <p className="muted">
                  {t(
                    "最多 30 页、20 MB。保留主要版式、配色和 Logo，预览确认后保存。视觉分析会产生模型费用。",
                    "Up to 30 slides and 20 MB. Preserve layouts, colors and logos; review before saving. Vision analysis incurs model charges.",
                  )}
                </p>
                <TemplateGrid
                  templates={templates}
                  chosen={templateID}
                  choose={setTemplateID}
                  preview={(item) => void openTemplate(item)}
                  t={t}
                />
              </>
            )}
            {view === "works" && (
              <>
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">MADE BY YOU</p>
                    <h1>{t("我的作品", "My presentations")}</h1>
                  </div>
                  <button className="primary" onClick={() => setView("create")}>
                    <Plus size={17} />
                    {t("新建演示", "New presentation")}
                  </button>
                </div>
                <div className="work-grid">
                  {works.map((work, i) => (
                    <article className="work-card" key={work.id}>
                      <button
                        className={`work-cover theme-${i % 6}`}
                        onClick={() => void openDeck(work.id)}
                      >
                        <span>{String(i + 1).padStart(2, "0")}</span>
                        <h2>{work.title}</h2>
                        <ArrowUpRight />
                      </button>
                      <div className="work-meta">
                        <div>
                          <strong>{work.title}</strong>
                          <small>
                            {work.pages} {t("页", "slides")} ·{" "}
                            {new Date(work.updated).toLocaleDateString()}
                          </small>
                        </div>
                        <button
                          className="danger"
                          onClick={() =>
                            void perform(async () => {
                              await api("/decks/" + work.id, {
                                method: "DELETE",
                              });
                              await refresh();
                            })
                          }
                        >
                          {t("删除", "Delete")}
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
                {!works.length && (
                  <Empty
                    text={t(
                      "还没有作品，从一个想法开始。",
                      "No presentations yet. Start with an idea.",
                    )}
                  />
                )}
              </>
            )}
            {view === "jobs" && (
              <>
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">EVERY STEP, SAVED</p>
                    <h1>{t("生成任务", "Activity")}</h1>
                    <p className="muted">
                      {t(
                        "刷新页面不会丢失进度。已完成内容会保留，费用按实际结算。",
                        "Progress survives refreshes. Completed content is saved and usage is settled through pipiapi.",
                      )}
                    </p>
                  </div>
                </div>
                <div className="jobs">
                  {jobs.map((job) => (
                    <JobRow
                      key={job.id}
                      job={job}
                      t={t}
                      open={() => job.deck_id && void openDeck(job.deck_id)}
                      action={(action) =>
                        void perform(async () => {
                          await api(`/jobs/${job.id}/${action}`, {
                            method: "POST",
                          });
                          await refresh();
                        })
                      }
                    />
                  ))}
                </div>
                {!jobs.length && (
                  <Empty text={t("暂无生成任务", "No jobs yet")} />
                )}
              </>
            )}
            {view === "admin" && policy && (
              <>
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">SITE CONTROLS</p>
                    <h1>{t("站点管理", "Administration")}</h1>
                  </div>
                </div>
                <section className="admin-form">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={policy.enabled}
                      onChange={(e) =>
                        setPolicy({ ...policy, enabled: e.target.checked })
                      }
                    />
                    {t("允许创建生成任务", "Allow new generation")}
                  </label>
                  <label>
                    {t(
                      "已验证的文字/视觉模型，每行一个",
                      "Validated text/vision models, one per line",
                    )}
                    <textarea
                      value={policy.text_models.join("\n")}
                      onChange={(e) =>
                        setPolicy({
                          ...policy,
                          text_models: e.target.value.split("\n"),
                        })
                      }
                    />
                  </label>
                  <label>
                    {t(
                      "已验证的图片模型，每行一个",
                      "Validated image models, one per line",
                    )}
                    <textarea
                      value={policy.image_models.join("\n")}
                      onChange={(e) =>
                        setPolicy({
                          ...policy,
                          image_models: e.target.value.split("\n"),
                        })
                      }
                    />
                  </label>
                  {(
                    [
                      "generation_concurrency",
                      "export_concurrency",
                      "user_running",
                      "user_queued",
                      "upload_mb",
                      "storage_mb",
                    ] as const
                  ).map((key, i) => (
                    <label key={key}>
                      {t(
                        [
                          "全站生成并发",
                          "全站导出并发",
                          "每用户执行中任务",
                          "每用户排队任务",
                          "单文件上限（MB）",
                          "每用户存储（MB）",
                        ][i],
                        [
                          "Generation concurrency",
                          "Export concurrency",
                          "Running jobs per user",
                          "Queued jobs per user",
                          "Upload limit (MB)",
                          "Storage per user (MB)",
                        ][i],
                      )}
                      <input
                        type="number"
                        min={1}
                        value={policy[key]}
                        onChange={(e) =>
                          setPolicy({
                            ...policy,
                            [key]: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                  ))}
                  <p className="muted small">
                    {t(
                      "启用前，请完成文字结构化输出、视觉输入与图片生成验证。模型调用统一经 pipiapi，不支持其他模型地址。",
                      "Before enabling, validate structured text, vision input and image generation. All model calls use pipiapi.",
                    )}
                  </p>
                  <button
                    className="primary"
                    onClick={() =>
                      void perform(async () => {
                        await api("/admin", {
                          method: "PUT",
                          body: JSON.stringify({
                            ...policy,
                            text_models: policy.text_models
                              .map((m) => m.trim())
                              .filter(Boolean),
                            image_models: policy.image_models
                              .map((m) => m.trim())
                              .filter(Boolean),
                          }),
                        });
                        await refresh();
                      })
                    }
                  >
                    {t("保存配置", "Save settings")}
                  </button>
                </section>
                {adminConfig && (
                  <section className="admin-form">
                    <h2>{t("内置模板", "Built-in templates")}</h2>
                    {adminConfig.templates.map((template) => (
                      <label className="check" key={template.id}>
                        <input
                          type="checkbox"
                          checked={template.enabled}
                          disabled={busy}
                          onChange={(event) => {
                            const enabled = event.target.checked;
                            void perform(async () => {
                              setAdminConfig((current) =>
                                current
                                  ? {
                                      ...current,
                                      templates: current.templates.map(
                                        (item) =>
                                          item.id === template.id
                                            ? { ...item, enabled }
                                            : item,
                                      ),
                                    }
                                  : current,
                              );
                              try {
                                await api(
                                  `/admin/templates/${template.id}?enabled=${enabled}`,
                                  { method: "PUT" },
                                );
                              } catch (error) {
                                setAdminConfig((current) =>
                                  current
                                    ? {
                                        ...current,
                                        templates: current.templates.map(
                                          (item) =>
                                            item.id === template.id
                                              ? {
                                                  ...item,
                                                  enabled: template.enabled,
                                                }
                                              : item,
                                        ),
                                      }
                                    : current,
                                );
                                throw error;
                              }
                              await refresh();
                            });
                          }}
                        />
                        {template.name}
                      </label>
                    ))}
                    <h2>{t("任务状态", "Job status")}</h2>
                    <p>
                      {t(
                        "排队 / 执行中 / 失败 / 消费待确认",
                        "Queued / Running / Failed / Needs review",
                      )}
                      :{" "}
                      {["queued", "running", "failed", "awaiting_confirmation"]
                        .map((status) => adminConfig.jobs[status] || 0)
                        .join(" / ")}
                    </p>
                    {adminConfig.oldest_queued_at && (
                      <p>
                        {t("最早排队时间", "Oldest queued job")}:{" "}
                        {new Date(
                          adminConfig.oldest_queued_at,
                        ).toLocaleString()}
                      </p>
                    )}
                    <button
                      disabled={busy}
                      onClick={() =>
                        void perform(async () =>
                          setAdminConfig(await api<AdminConfig>("/admin")),
                        )
                      }
                    >
                      {t("刷新任务状态", "Refresh job status")}
                    </button>
                  </section>
                )}
              </>
            )}
            {view === "files" && (
              <>
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">PRIVATE STORAGE</p>
                    <h1>{t("文件空间", "Storage")}</h1>
                    <p className="muted">
                      {(profile.profile.stored_bytes / 1024 / 1024).toFixed(1)}{" "}
                      MB / {(profile.storage_limit / 1024 / 1024).toFixed(0)} MB
                    </p>
                  </div>
                </div>
                {files.map((file) => (
                  <div className="file-row" key={file.id}>
                    <a href={"/api/assets/" + file.id}>{file.name}</a>
                    <span>{(file.size / 1024).toFixed(0)} KB</span>
                    <button
                      onClick={() =>
                        void perform(async () => {
                          await api("/assets/" + file.id, { method: "DELETE" });
                          setFiles(await api("/assets"));
                          await refresh();
                        })
                      }
                    >
                      {t("删除", "Delete")}
                    </button>
                  </div>
                ))}
              </>
            )}
          </div>
        )}
      </main>
      {preview && (
        <div className="modal-backdrop">
          <section
            className="template-modal"
            role="dialog"
            aria-modal="true"
            aria-label={t("模板预览", "Template preview")}
          >
            <div className="section-heading">
              <h2>{preview.data.name}</h2>
              <button
                aria-label={t("关闭", "Close")}
                onClick={() => setPreview(null)}
              >
                <X />
              </button>
            </div>
            <p className="muted">
              {t("转换后的可编辑布局", "Converted editable layout")} ·{" "}
              {previewIndex + 1}/{preview.data.layouts.length}
            </p>
            <Scene slide={preview.data.layouts[previewIndex]} />
            <div className="modal-pages">
              {preview.data.layouts.map((_, i) => (
                <button
                  key={i}
                  onClick={() => setPreviewIndex(i)}
                  className={previewIndex === i ? "active" : ""}
                >
                  {i + 1}
                </button>
              ))}
            </div>
            {preview.data.previews?.[previewIndex] && (
              <a
                href={"/api/assets/" + preview.data.previews[previewIndex]}
                target="_blank"
                rel="noreferrer"
              >
                {t("查看原始 PPT 预览 ↗", "View original PPT preview ↗")}
              </a>
            )}
            <p>{preview.data.analysis?.description}</p>
            <details>
              <summary>
                {t("字体替代与导入范围", "Font substitutions & import scope")}
              </summary>
              {preview.data.warnings.map((warning, i) => (
                <p className="small" key={i}>
                  {warning}
                </p>
              ))}
            </details>
            <div className="modal-actions">
              {!preview.builtin && (
                <button
                  className="danger"
                  onClick={() =>
                    void perform(async () => {
                      await api("/templates/" + preview.id, {
                        method: "DELETE",
                      });
                      setPreview(null);
                      await refresh();
                    })
                  }
                >
                  {t("删除模板", "Delete template")}
                </button>
              )}
              <button
                className="primary"
                onClick={() =>
                  void perform(async () => {
                    if (!preview.confirmed)
                      await api(`/templates/${preview.id}/confirm`, {
                        method: "POST",
                      });
                    setTemplateID(preview.id);
                    setPreview(null);
                    setView("create");
                    await refresh();
                  })
                }
              >
                <Check size={17} />
                {preview.confirmed
                  ? t("使用这个模板", "Use template")
                  : t("确认预览并保存", "Confirm & save")}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function TemplateGrid({
  templates,
  chosen,
  choose,
  preview,
  t,
}: {
  templates: Template[];
  chosen: string;
  choose: (id: string) => void;
  preview: (template: Template) => void;
  t: (zh: string, en: string) => string;
}) {
  return (
    <div className="template-grid">
      {templates.map((template, i) => (
        <article
          className={`template-card ${chosen === template.id ? "chosen" : ""}`}
          key={template.id}
        >
          <button
            className={`template-cover theme-${i % 6} ${template.thumbnail ? "has-thumbnail" : ""}`}
            onClick={() =>
              template.confirmed ? choose(template.id) : preview(template)
            }
          >
            {template.thumbnail && (
              <Image
                className="template-thumbnail"
                src={`/api/template-assets/${template.thumbnail}`}
                alt=""
                aria-hidden="true"
                fill
                unoptimized
                sizes="(max-width: 900px) 100vw, 33vw"
              />
            )}
            <span>PIPI / {String(i + 1).padStart(2, "0")}</span>
            <h3>{template.name.split(" / ")[0]}</h3>
            <div className="template-lines">
              <i />
              <i />
              <i />
            </div>
            <div className="template-circle" />
            {chosen === template.id && (
              <span className="selected-badge">
                <Check size={15} />
              </span>
            )}
          </button>
          <div className="template-meta">
            <strong>{template.name}</strong>
            <button onClick={() => preview(template)}>
              {template.confirmed
                ? t("预览", "Preview")
                : t("待确认", "Review")}
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}

function JobRow({
  job,
  t,
  open,
  action,
}: {
  job: Job;
  t: (zh: string, en: string) => string;
  open: () => void;
  action: (name: string) => void;
}) {
  const [usage, setUsage] = useState<{
    quota: number;
    quota_per_unit?: number;
    settling: boolean;
  } | null>(null);
  const [usageError, setUsageError] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refreshUsage() {
      try {
        const result = await api<{
          quota: number;
          quota_per_unit?: number;
          settling: boolean;
        }>(`/jobs/${job.id}/usage`);
        if (cancelled) return;
        setUsage(result);
        setUsageError(false);
        if (result.settling || ["queued", "running"].includes(job.status))
          timer = setTimeout(() => void refreshUsage(), 15000);
      } catch {
        if (!cancelled) {
          setUsageError(true);
          timer = setTimeout(() => void refreshUsage(), 30000);
        }
      }
    }
    void refreshUsage();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [job.id, job.status]);
  const kinds: Record<string, [string, string]> = {
    outline: ["生成大纲", "Outline"],
    generate: ["生成演示", "Generate"],
    rewrite: ["重写页面", "Rewrite"],
    template: ["导入模板", "Import template"],
    export: ["导出作品", "Export"],
  };
  const statuses: Record<string, [string, string]> = {
    queued: ["排队中", "Queued"],
    running: ["处理中", "Running"],
    complete: ["已完成", "Complete"],
    failed: ["失败", "Failed"],
    cancelled: ["已取消", "Cancelled"],
    awaiting_confirmation: ["消费待确认", "Needs review"],
  };
  return (
    <article className="job-row">
      <div className={"job-icon " + job.status}>
        <Presentation size={22} />
      </div>
      <div className="job-info">
        <strong>{t(...(kinds[job.kind] || ["任务", "Job"]))}</strong>
        <p>
          {t(...(statuses[job.status] || ["未知", "Unknown"]))}{" "}
          {job.cursor > 0 && `· ${job.cursor} ${t("页已保存", "slides saved")}`}
        </p>
        {job.error && <p className="job-error">{errorText(job.error, t)}</p>}
        <small>{new Date(job.updated).toLocaleString()}</small>
      </div>
      <div className="job-cost">
        {usage ? (
          <>
            <strong>
              {(usage.quota / (usage.quota_per_unit || 1)).toFixed(5)}
            </strong>
            <small>
              {usage.settling
                ? t("结算中", "Settling")
                : t("pipiapi 额度单位", "pipiapi quota units")}
            </small>
          </>
        ) : (
          <small>
            {usageError ? t("消费查询暂不可用", "Usage unavailable") : "—"}
          </small>
        )}
      </div>
      <div className="job-actions">
        {job.deck_id && <button onClick={open}>{t("打开", "Open")}</button>}
        {job.result.asset_id && (
          <a
            className="button-link"
            href={"/api/assets/" + job.result.asset_id}
          >
            {t("下载", "Download")}
          </a>
        )}
        {active(job) && (
          <button onClick={() => action("cancel")}>
            {t("取消", "Cancel")}
          </button>
        )}
        {["failed", "cancelled"].includes(job.status) && (
          <button onClick={() => action("resume")}>
            {t("恢复", "Resume")}
          </button>
        )}
      </div>
    </article>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="empty">
      <FileStack size={36} />
      <p>{text}</p>
    </div>
  );
}
function errorText(error: string, t: (zh: string, en: string) => string) {
  const messages: Record<string, [string, string]> = {
    presentation_changed_after_pause: [
      "暂停后作品已有手动修改。为保留这些修改，不能恢复旧任务；可继续手动编辑或单页重写。",
      "This presentation was edited after the job stopped. Continue editing or rewrite a slide to preserve those changes.",
    ],
    site_paused: [
      "站点暂停创建新任务，请稍后重试。",
      "New generation is paused.",
    ],
    queue_full: [
      "排队任务已满，请等待现有任务完成。",
      "Your queue is full. Wait for an existing job.",
    ],
    authorization_expired: [
      "pipiapi 授权已失效，请重新登录。",
      "Your pipiapi authorization expired. Please sign in again.",
    ],
    model_result_unconfirmed: [
      "模型请求可能已经消费，系统不会自动重试。请核对 pipiapi 记录后再决定是否新建任务。",
      "A model request may have been charged. It will not be retried automatically. Review pipiapi usage before creating another job.",
    ],
    model_result_requires_manual_review: [
      "此步骤需要人工确认消费，不能自动恢复。",
      "This step requires a usage review before any new call.",
    ],
    model_not_available: [
      "所选模型当前不可用，请检查账号权限与推荐模型配置。",
      "The selected model is unavailable. Check account access and site settings.",
    ],
    invalid_outline_response: [
      "模型返回的大纲格式不完整。点击“恢复”可使用已保存的模型结果继续处理。",
      "The model returned an incomplete outline. Click Resume to continue with the saved result.",
    ],
    version_conflict: [
      "作品已有新版本。请保存本地修改后重新打开，避免覆盖。",
      "A newer version exists. Preserve your edits and reopen the presentation.",
    ],
    deck_busy: [
      "作品仍在生成，请等待或取消任务后编辑。",
      "This presentation is still being generated. Wait or cancel the job.",
    ],
    upload_too_large: [
      "文件超过上传大小限制。",
      "The file exceeds the upload limit.",
    ],
    storage_quota_exceeded: [
      "文件空间已满，请先删除不再使用的文件。",
      "Storage is full. Delete unused files first.",
    ],
    file_still_in_use: [
      "这个文件仍被作品、模板或任务使用。",
      "This file is still used by a presentation, template or job.",
    ],
    gateway_unavailable: [
      "pipiapi 暂时不可用，请稍后重试。",
      "pipiapi is temporarily unavailable.",
    ],
    gateway_rejected_403: [
      "模型调用被拒绝，请检查模型权限或套餐限制。",
      "Model access was rejected. Check permissions or subscription limits.",
    ],
    gateway_rejected_402: [
      "账户额度不足，请前往 pipiapi 查看。",
      "Insufficient quota. Check your pipiapi account.",
    ],
  };
  return messages[error]
    ? t(...messages[error])
    : t("操作未完成：", "Operation failed: ") + error;
}
