// Study page logic. The URL path picks the condition: /baseline or /ai.
// Flow: welcome -> trials (one at a time) -> survey -> done.

const condition = { "/baseline": "baseline", "/ai": "ai" }[location.pathname];
const isAI = condition === "ai";
const $ = (id) => document.getElementById(id);

let session = null;       // {session_id, trials: [{id, tweet}], condition_order}
let index = 0;            // which trial is on screen (0-based)
let answered = [];        // answered[i] = {answer, certainty, comment}; tweets are answered in order
let showToken = 0;        // bumps on every showTrial, so a stale one (after Back) stops early
let shownAt = 0;          // when the tweet appeared (response time is measured from here)
let aiWaitMs = null;      // AI condition: how long the suggestion took to appear
let awayCount = 0;        // times the page lost focus while this tweet was shown
const aiRequests = {};    // trial id -> Promise of the AI suggestion (so each is fetched once)
const pageLoadedAt = performance.now();
const MIN_VIEW_MS = 1000; // answers unlock at least this long after a tweet appears
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------- helpers
function show(id) {
  document.querySelectorAll("section").forEach((s) => (s.hidden = s.id !== id));
  window.scrollTo(0, 0);
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body && JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function showError(err) {
  console.error(err);
  $("error").textContent = "Something went wrong. Please reload the page and start again. (" + err.message + ")";
  $("error").hidden = false;
}

// Ask the server for the AI suggestion for trial i, at most once. Called ahead of time
// (prefetch) so the suggestion is usually ready when the trial appears.
function fetchAI(i) {
  const trial = session.trials[i];
  if (!isAI || !trial || trial.id === "attn") return null;
  aiRequests[trial.id] ??= api("GET", `/api/sessions/${session.session_id}/ai/${trial.id}`);
  return aiRequests[trial.id];
}

function setAnswersEnabled(on) {
  document.querySelectorAll(".answers button").forEach((b) => (b.disabled = !on));
  $("back").disabled = !on;
}

// ---------------------------------------------------------------- screens
function welcome() {
  document.querySelectorAll(".ai-only").forEach((el) => (el.hidden = !isAI));
  document.querySelectorAll(".baseline-only").forEach((el) => (el.hidden = isAI));
  api("GET", "/api/config").then((c) =>
    document.querySelectorAll(".n-trials").forEach((el) => (el.textContent = c.n_items)));
  const input = $("uniqname");
  input.value = new URLSearchParams(location.search).get("pid") || "";
  const update = () => ($("start").disabled = !input.value.trim());
  input.addEventListener("input", update);
  update();
  show("welcome");
}

async function start() {
  $("start").disabled = true;
  try {
    session = await api("POST", "/api/sessions", {
      participant: $("uniqname").value.trim(),
      condition,
      instructions_ms: Math.round(performance.now() - pageLoadedAt),
    });
  } catch (err) {
    $("start").disabled = false;
    return showError(err);
  }
  index = 0;
  answered = [];
  fetchAI(0);
  fetchAI(1);
  showTrial();
}

async function showTrial() {
  const token = ++showToken;
  const trial = session.trials[index];
  const total = session.trials.length;
  const previous = answered[index]; // set if the participant came back to this tweet
  $("counter").textContent = `Tweet ${index + 1} of ${total}`;
  $("bar").style.width = `${(100 * answered.length) / total}%`;
  $("tweet").textContent = trial.tweet;
  $("trial-comment").value = previous?.comment || "";
  document.querySelector(".trial-comment").open = Boolean(previous?.comment);
  $("revisit-note").hidden = !previous;
  $("back").hidden = index === 0;
  document.querySelectorAll(".answers button").forEach((b) => {
    b.classList.remove("selected", "previous");
    if (previous && b.dataset.answer === previous.answer && b.dataset.certainty === previous.certainty) {
      b.classList.add("previous");
    }
  });
  setAnswersEnabled(false);
  show("trial");
  shownAt = performance.now();
  aiWaitMs = null;
  awayCount = 0;
  const minView = sleep(MIN_VIEW_MS);

  const pending = fetchAI(index);
  fetchAI(index + 1); // prefetch the next one while this one is being read
  $("ai-box").hidden = !pending;
  if (pending) {
    // AI condition: the answer buttons stay disabled until the suggestion is on screen.
    $("ai-loading").hidden = false;
    $("ai-result").hidden = true;
    try {
      const s = await pending;
      if (token !== showToken) return; // participant pressed Back meanwhile
      $("ai-label").textContent = s.label === "sarcastic" ? "Sarcastic" : "Not sarcastic";
      $("ai-conf").textContent = s.confidence;
      $("ai-expl").textContent = s.explanation;
      $("ai-loading").hidden = true;
      $("ai-result").hidden = false;
      aiWaitMs = Math.round(performance.now() - shownAt);
    } catch (err) {
      return showError(err);
    }
  }
  await minView;
  if (token !== showToken) return;
  setAnswersEnabled(true);
}

function goBack(toIndex) {
  index = toIndex;
  showTrial();
}

async function answer(button) {
  const rt_ms = Math.round(performance.now() - shownAt);
  setAnswersEnabled(false);
  button.classList.add("selected"); // show which answer was recorded
  let result;
  try {
    [result] = await Promise.all([
      api("POST", `/api/sessions/${session.session_id}/responses`, {
        position: index + 1,
        answer: button.dataset.answer,
        certainty: button.dataset.certainty,
        rt_ms,
        ai_wait_ms: aiWaitMs,
        away_count: awayCount,
        comment: $("trial-comment").value.trim() || null,
      }),
      sleep(300), // keep the highlight visible briefly
    ]);
  } catch (err) {
    setAnswersEnabled(true);
    return showError(err);
  }
  answered[index] = { answer: button.dataset.answer, certainty: button.dataset.certainty,
                      comment: $("trial-comment").value.trim() };
  if (result.failed_attention) return show("failed");
  index = answered.length; // continue at the first unanswered tweet (also after going Back)
  if (index < session.trials.length) showTrial();
  else survey();
}

function survey() {
  // Build the 1-7 radio buttons for each visible scale question.
  document.querySelectorAll("fieldset.scale").forEach((fs) => {
    if (fs.querySelector(".points")) return;
    const points = document.createElement("div");
    points.className = "points";
    for (let v = 1; v <= 7; v++) {
      points.insertAdjacentHTML("beforeend",
        `<label><input type="radio" name="${fs.dataset.name}" value="${v}" ${fs.hidden ? "" : "required"}>${v}</label>`);
    }
    fs.querySelector(".ends").before(points);
  });
  show("survey");
}

async function submitSurvey(event) {
  event.preventDefault();
  const form = new FormData($("survey-form"));
  const num = (name) => (form.get(name) ? Number(form.get(name)) : null);
  try {
    await api("POST", `/api/sessions/${session.session_id}/survey`, {
      mental_demand: num("mental_demand"),
      ai_trust: num("ai_trust"),
      ai_helpful: num("ai_helpful"),
      comment: $("comment").value.trim() || null,
    });
  } catch (err) {
    return showError(err);
  }
  const pid = encodeURIComponent($("uniqname").value.trim());
  const other = isAI ? "/baseline" : "/ai";
  $("next-step").innerHTML = session.condition_order === 1
    ? `If you haven't yet, please also do <a href="${other}?pid=${pid}">the other version of this study</a> (you'll get different tweets).`
    : "You've finished both versions. Please add your comments in the class spreadsheet. You can close this page now.";
  show("done");
}

// ---------------------------------------------------------------- wiring
$("start").addEventListener("click", start);
$("uniqname").addEventListener("keydown", (e) => e.key === "Enter" && !$("start").disabled && start());
$("restart").addEventListener("click", () =>
  (location.href = `${location.pathname}?pid=${encodeURIComponent($("uniqname").value.trim())}`));
$("survey-form").addEventListener("submit", submitSurvey);
$("back").addEventListener("click", () => goBack(index - 1));
$("survey-back").addEventListener("click", () => goBack(session.trials.length - 1));
document.querySelectorAll(".answers button").forEach((b) => b.addEventListener("click", () => answer(b)));

// Keys 1-4 press the matching answer button (same lock as clicking; ignored while typing).
document.addEventListener("keydown", (e) => {
  if ($("trial").hidden || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.matches("input, textarea")) return;
  const button = document.querySelectorAll(".answers button")[Number(e.key) - 1];
  if (button && !button.disabled) answer(button);
});

// Count how often the participant leaves the page (other tab or window) during a tweet.
window.addEventListener("blur", () => {
  if (!$("trial").hidden) awayCount += 1;
});

if (condition) welcome();
else show("home");
