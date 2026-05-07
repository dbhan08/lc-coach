// Injected into https://leetcode.com/problems/* pages.
// Responsibility: extract the current problem (slug, title, difficulty,
// statement) from the DOM and respond to GET_PROBLEM messages from the side
// panel.

(function () {
  function slugFromUrl() {
    const m = window.location.pathname.match(/^\/problems\/([^\/]+)/);
    return m ? m[1] : null;
  }

  function titleFromDom() {
    // Modern LeetCode: title is in an <a> with class containing "no-underline"
    // or a heading near the description. document.title is the most reliable
    // immediate signal: "Two Sum - LeetCode".
    const docTitle = document.title || "";
    const stripped = docTitle.replace(/\s*-\s*LeetCode.*$/, "").trim();
    if (stripped) return stripped;
    // Fallback to og:title
    const og = document.querySelector('meta[property="og:title"]');
    if (og && og.content) {
      return og.content.replace(/\s*-\s*LeetCode.*$/, "").trim();
    }
    return null;
  }

  function difficultyFromDom() {
    // Look for an element whose text is exactly Easy/Medium/Hard. LeetCode
    // colors these via classes, but the classnames change; the literal text
    // is the cheapest reliable signal.
    const candidates = document.querySelectorAll(
      'div[class*="difficulty"], div[class*="Difficulty"], span[class*="difficulty"], span[class*="Difficulty"]',
    );
    for (const el of candidates) {
      const t = (el.textContent || "").trim();
      if (t === "Easy" || t === "Medium" || t === "Hard") return t;
    }
    // Fallback: scan a small set of nearby spans/divs for matching text.
    const allSmall = document.querySelectorAll("span, div");
    for (const el of allSmall) {
      const t = (el.textContent || "").trim();
      if (t === "Easy" || t === "Medium" || t === "Hard") return t;
    }
    return null;
  }

  function statementFromDom() {
    // Primary: the description container. LeetCode rotates classnames; try a
    // few known data-* attributes and visible-content selectors before falling
    // back to meta description.
    const selectors = [
      'div[data-track-load="description_content"]',
      'div[class*="elfjS"]',
      'div[class*="description__"]',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.innerText && el.innerText.length > 80) {
        return el.innerText.trim();
      }
    }
    // Fallback: meta description (truncated but present early in load).
    const meta = document.querySelector('meta[name="description"]');
    if (meta && meta.content) {
      return meta.content.trim();
    }
    return null;
  }

  function tagsFromDom() {
    // LeetCode shows topic tags ("Array", "Hash Table") in a "Topics" expandable
    // section. They're usually anchor tags pointing to /tag/<slug>/. Grab those.
    const anchors = document.querySelectorAll('a[href^="/tag/"]');
    const seen = new Set();
    const out = [];
    anchors.forEach((a) => {
      const t = (a.textContent || "").trim();
      if (t && !seen.has(t)) {
        seen.add(t);
        out.push(t);
      }
    });
    return out;
  }

  function extractProblem() {
    return {
      slug: slugFromUrl(),
      title: titleFromDom(),
      difficulty: difficultyFromDom(),
      statement: statementFromDom(),
      tags: tagsFromDom(),
      url: window.location.href,
    };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "GET_PROBLEM") {
      sendResponse(extractProblem());
      return false;
    }
    return false;
  });
})();
