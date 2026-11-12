const fileInput = document.querySelector("#file-input");
const dropzone = document.querySelector("#dropzone");
const fileName = document.querySelector("#file-name");
const checkButton = document.querySelector("#check-button");
const checkSpinner = document.querySelector("#check-spinner");
const checkLabel = document.querySelector("#check-label");
const steps = document.querySelector("#status-steps");
const progress = document.querySelector("#progress");
const statusMessage = document.querySelector("#status-message");
const labelCount = document.querySelector("#label-count");
const labelList = document.querySelector("#label-list");
const previewPanel = document.querySelector("#preview-panel");
const preview = document.querySelector("#preview");
const downloadLink = document.querySelector("#download-link");

const MAX_BYTES = 16 * 1024 * 1024;
let selectedFile = null;

fileInput.addEventListener("change", () => {
  const file = fileInput.files && fileInput.files[0];
  if (file) {
    selectFile(file);
  }
});

dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropzone.classList.add("is-dragging");
});

dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("is-dragging");
});

dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropzone.classList.remove("is-dragging");
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  if (!file) {
    return;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  selectFile(file);
});

checkButton.addEventListener("click", () => {
  if (selectedFile) {
    runCheck(selectedFile);
  }
});

function selectFile(file) {
  clearResults();
  if (!file.name.toLowerCase().endsWith(".docx")) {
    selectedFile = null;
    checkButton.disabled = true;
    fileName.textContent = "No file selected.";
    setState("idle");
    setMessage("Only .docx templates can be checked.", "error");
    return;
  }
  if (file.size > MAX_BYTES) {
    selectedFile = null;
    checkButton.disabled = true;
    fileName.textContent = "No file selected.";
    setState("idle");
    setMessage("That file is larger than 16 MB.", "error");
    return;
  }
  selectedFile = file;
  fileName.textContent = file.name;
  flash(fileName);
  checkButton.disabled = false;
  setState("ready");
  setMessage("Ready to check " + file.name + ".", "info");
}

function clearResults() {
  labelCount.textContent = "0";
  labelList.replaceChildren();
  const waiting = document.createElement("li");
  waiting.className = "empty-note";
  waiting.textContent = "Labels appear here after the check runs.";
  labelList.append(waiting);
  preview.replaceChildren();
  previewPanel.hidden = true;
  downloadLink.hidden = true;
  downloadLink.removeAttribute("href");
}

async function runCheck(file) {
  setState("running");
  setMessage("Reading tags, rendering test values, and building the preview.", "info");
  checkButton.disabled = true;
  checkSpinner.classList.remove("hidden");
  checkLabel.textContent = "Checking...";

  const body = new FormData();
  body.append("file", file, file.name);

  try {
    const response = await fetch("/upload-and-check", { method: "POST", body });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      setState("error");
      setMessage("The checker returned an unexpected response.", "error");
      return;
    }
    // #region agent log
    fetch('http://127.0.0.1:7496/ingest/28d93795-f646-4e40-a963-ae0a7836d642',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'d2d014'},body:JSON.stringify({sessionId:'d2d014',hypothesisId:'E',location:'app.js:runCheck',message:'check response',data:{status:response.status,ok:!!(payload&&payload.ok),hasPreview:!!(payload&&payload.preview_html),previewLength:payload&&payload.preview_html?payload.preview_html.length:0,jobId:payload&&payload.job_id,msg:payload&&payload.message},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
    if (!response.ok || !payload.ok) {
      showLabels(payload.labels || []);
      setState("error");
      setMessage(payload.message || "The template could not be checked.", "error");
      return;
    }
    showLabels(payload.labels || []);
    preview.innerHTML = payload.preview_html || "";
    previewPanel.hidden = false;
    downloadLink.hidden = false;
    downloadLink.href = "/download-processed?job_id=" + encodeURIComponent(payload.job_id);
    downloadLink.textContent = "Download " + (payload.download_name || "processed.docx");
    setState("success");
    setMessage(payload.message || "Template checked.", "success");
  } catch (_error) {
    setState("error");
    setMessage("Could not reach the checker. Is the app still running?", "error");
  } finally {
    checkSpinner.classList.add("hidden");
    checkLabel.textContent = "D-GAY Checker";
    checkButton.disabled = selectedFile === null;
  }
}

function showLabels(labels) {
  labelCount.textContent = String(labels.length);
  labelList.replaceChildren();
  if (!labels.length) {
    const empty = document.createElement("li");
    empty.className = "empty-note";
    empty.textContent = "No placeholders were found.";
    labelList.append(empty);
    return;
  }
  for (const label of labels) {
    const item = document.createElement("li");
    const code = document.createElement("code");
    code.textContent = "{{ " + label + " }}";
    item.append(code);
    labelList.append(item);
  }
}

function setState(state) {
  steps.dataset.state = state;
  progress.hidden = state !== "running";
}

function setMessage(text, tone) {
  statusMessage.textContent = text;
  statusMessage.dataset.tone = tone;
  flash(statusMessage);
}

function flash(element) {
  element.classList.remove("is-fresh");
  void element.offsetWidth;
  element.classList.add("is-fresh");
}
