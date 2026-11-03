import { useEffect, useMemo, useRef, useState } from "react";
import type { Live } from "../api";
import { DOC_ARTICLES, searchDocs, type DocPage, type DocSection } from "../docs-content";
import { Guide } from "./Guide";

function Command({ label, text }: { label: string; text: string }) {
  const [feedback, setFeedback] = useState("");
  useEffect(() => {
    if (!feedback) return;
    const timer = window.setTimeout(() => setFeedback(""), 5000);
    return () => window.clearTimeout(timer);
  }, [feedback]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setFeedback("Copied. Paste into a terminal at the repository root.");
    } catch {
      setFeedback("Copy unavailable. Select the command below and copy it manually.");
    }
  };

  return (
    <div className="docs-command">
      <div className="docs-command-heading">
        <span>{label}</span>
        <button onClick={copy} aria-label={`Copy command: ${label}`}>
          {feedback.startsWith("Copied") ? "Copied ✓" : "Copy"}
        </button>
      </div>
      <pre tabIndex={0} aria-label={label}><code>{text}</code></pre>
      <p className="docs-copy-feedback" role="status">{feedback}</p>
    </div>
  );
}

function Section({ section, onNavigate }: { section: DocSection; onNavigate: (page: DocPage) => void }) {
  return (
    <section className="docs-section">
      <h3>{section.title}</h3>
      {section.paragraphs?.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
      {section.steps && <ol>{section.steps.map((step) => <li key={step}>{step}</li>)}</ol>}
      {section.bullets && <ul>{section.bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}</ul>}
      {section.table && (
        <div className="docs-table-wrap" tabIndex={0} role="region" aria-label={`${section.title} reference table`}>
          <table>
            <thead><tr>{section.table.headings.map((heading) => <th key={heading} scope="col">{heading}</th>)}</tr></thead>
            <tbody>{section.table.rows.map((row) => <tr key={row[0]}>{row.map((cell, index) => index === 0 ? <th key={index} scope="row">{cell}</th> : <td key={index}>{cell}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
      {section.command && <Command key={section.command.text} {...section.command} />}
      {section.note && <aside className="docs-callout"><p>{section.note}</p></aside>}
      {section.action && <button className="docs-next" onClick={() => onNavigate(section.action!.page)}>{section.action.label} <span aria-hidden="true">→</span></button>}
    </section>
  );
}

export function Docs({ live, onNavigate }: { live: Live; onNavigate: (page: DocPage) => void }) {
  const [topic, setTopic] = useState(() => {
    const id = window.location.hash.split("/")[1];
    return DOC_ARTICLES.some((item) => item.id === id) ? id : "start";
  });
  const [query, setQuery] = useState("");
  const matches = useMemo(() => searchDocs(query), [query]);
  const article = matches.find((candidate) => candidate.id === topic) ?? matches[0];
  const title = useRef<HTMLHeadingElement>(null);
  const focusArticle = useRef(false);
  useEffect(() => {
    if (focusArticle.current) {
      title.current?.focus({ preventScroll: true });
      focusArticle.current = false;
    }
  }, [article?.id]);

  useEffect(() => {
    const changed = () => {
      const id = window.location.hash.split("/")[1];
      if (window.location.hash.startsWith("#docs")) {
        setTopic(DOC_ARTICLES.some((item) => item.id === id) ? id : "start");
      }
    };
    window.addEventListener("hashchange", changed);
    window.addEventListener("popstate", changed);
    return () => { window.removeEventListener("hashchange", changed); window.removeEventListener("popstate", changed); };
  }, []);

  const choose = (next: string) => {
    if (article?.id === next) {
      title.current?.focus({ preventScroll: true });
    } else {
      focusArticle.current = true;
    }
    setTopic(next);
    if (window.location.hash !== `#docs/${next}`) window.history.pushState(null, "", `#docs/${next}`);
  };

  return (
    <div className="docs" data-testid="docs">
      <div className="docs-search">
        <label htmlFor="docs-search-input">What would you like to know?</label>
        <div className="docs-search-field">
          <input id="docs-search-input" type="search" placeholder="Search commands, ML, replay, errors…" value={query} onChange={(event) => setQuery(event.target.value)} />
          {query && <button onClick={() => setQuery("")} aria-label="Clear documentation search">Clear</button>}
        </div>
        {query && <p className="docs-search-count" role="status">{matches.length} {matches.length === 1 ? "topic" : "topics"} matching “{query}”</p>}
      </div>
      <div className="docs-layout">
        <aside className="docs-sidebar">
          <span className="eyebrow">IN THIS GUIDE</span>
          <nav className="docs-topics" aria-label="Documentation topics">
            {matches.map((item) => <button key={item.id} onClick={() => choose(item.id)} aria-current={item.id === article?.id ? "page" : undefined} data-testid={`docs-topic-${item.id}`}>{item.label}</button>)}
          </nav>
          <p className="docs-sidebar-note">Commands run from the repository root. Every demonstration controls your real local cluster.</p>
        </aside>
        <div className="docs-content">
          {article ? (
            <article aria-labelledby="docs-article-title">
              <header className="docs-article-header">
                <span className="eyebrow">{article.label}</span>
                <h2 id="docs-article-title" tabIndex={-1} ref={title}>{article.title}</h2>
                <p>{article.summary}</p>
              </header>
              {article.sections.map((section) => <Section key={`${article.id}-${section.title}`} section={section} onNavigate={onNavigate} />)}
            </article>
          ) : (
            <div className="docs-empty">
              <h2>No matching topics</h2>
              <p>Try “scaling”, “stop”, “replay” or “model”, or clear the search to browse all {DOC_ARTICLES.length} topics.</p>
              <button onClick={() => setQuery("")}>Browse all topics</button>
            </div>
          )}
          {/* Keep the guided demo mounted so topic changes never discard a step. */}
          <div className="docs-guided" hidden={article?.id !== "guided"}>
            <Guide live={live} />
          </div>
        </div>
      </div>
    </div>
  );
}
