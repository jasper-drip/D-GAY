const player = document.querySelector("#player");
const audio = document.querySelector("#player-audio");
const art = document.querySelector("#player-art");
const title = document.querySelector("#player-title");
const toggle = document.querySelector("#player-toggle");
const playIcon = document.querySelector("#player-icon-play");
const pauseIcon = document.querySelector("#player-icon-pause");
const previousButton = document.querySelector("#player-prev");
const nextButton = document.querySelector("#player-next");
const volume = document.querySelector("#player-volume");
const muteButton = document.querySelector("#player-mute");
const soundIcon = document.querySelector("#player-icon-sound");
const mutedIcon = document.querySelector("#player-icon-muted");
const meter = document.querySelector("#player-bars");
let bars = [];

const TRACKS = [
  { file: "Browsing_After_Hours", title: "Browsing After Hours" },
  { file: "Blue_Screen_Sunday", title: "Blue Screen Sunday" },
  { file: "Glass_Pane_Tides", title: "Glass Pane Tides" },
  { file: "Softened_Horizon", title: "Softened Horizon" },
];

const base = player.dataset.base.replace(/[^/]+$/, "");
const barFloor = 0.08;

let index = Math.floor(Math.random() * TRACKS.length);
let wantPlay = false;
let audioCtx = null;
let analyser = null;
let bins = null;
let frame = 0;
let unlockArmed = false;
let loadErrors = 0;
let savedVolume = 20;

function load(nextIndex) {
  index = (nextIndex + TRACKS.length) % TRACKS.length;
  const track = TRACKS[index];
  title.textContent = track.title;
  art.src = base + track.file + ".jpg";
  art.alt = track.title + " cover";
  audio.src = base + track.file + ".mp3";
}

function ensureGraph() {
  if (audioCtx) {
    return;
  }
  const Context = window.AudioContext || window.webkitAudioContext;
  audioCtx = new Context();
  const source = audioCtx.createMediaElementSource(audio);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.78;
  analyser.minDecibels = -90;
  analyser.maxDecibels = -22;
  source.connect(analyser);
  analyser.connect(audioCtx.destination);
  bins = new Uint8Array(analyser.frequencyBinCount);
}

function buildBars() {
  const count = Math.max(24, Math.round(meter.clientWidth / 12));
  if (count === bars.length) {
    return;
  }
  const fragment = document.createDocumentFragment();
  const created = [];
  for (let i = 0; i < count; i += 1) {
    const bar = document.createElement("span");
    fragment.appendChild(bar);
    created.push(bar);
  }
  meter.replaceChildren(fragment);
  bars = created;
  if (!frame) {
    restBars();
  }
}

function restBars() {
  for (const bar of bars) {
    bar.style.transform = "scaleY(" + barFloor + ")";
  }
}

function startMeter() {
  if (frame) {
    return;
  }
  const draw = () => {
    frame = requestAnimationFrame(draw);
    if (!analyser || !audioCtx || audioCtx.state !== "running" || bars.length < 2) {
      return;
    }
    analyser.getByteFrequencyData(bins);
    const last = bars.length - 1;
    const span = Math.floor(bins.length * 0.72);
    for (let i = 0; i < bars.length; i += 1) {
      const shaped = Math.pow(i / last, 1.15);
      const bin = Math.min(bins.length - 1, Math.round(shaped * span));
      const level = Math.pow(bins[bin] / 255, 0.75);
      const scale = barFloor + level * (1 - barFloor);
      bars[i].style.transform = "scaleY(" + scale.toFixed(3) + ")";
    }
  };
  draw();
}

function stopMeter() {
  if (frame) {
    cancelAnimationFrame(frame);
    frame = 0;
  }
  restBars();
}

function showPlaying() {
  player.classList.add("is-playing");
  toggle.setAttribute("aria-label", "Pause");
  playIcon.classList.add("hidden");
  pauseIcon.classList.remove("hidden");
  startMeter();
}

function showPaused() {
  player.classList.remove("is-playing");
  toggle.setAttribute("aria-label", "Play");
  playIcon.classList.remove("hidden");
  pauseIcon.classList.add("hidden");
  stopMeter();
}

function armUnlock() {
  if (unlockArmed) {
    return;
  }
  unlockArmed = true;
  const unlock = (event) => {
    const target = event.target;
    if (target && target.closest && target.closest("#player-toggle, #player-prev, #player-next, #player-mute")) {
      return;
    }
    document.removeEventListener("pointerdown", unlock, true);
    unlockArmed = false;
    play();
  };
  document.addEventListener("pointerdown", unlock, true);
}

function audible() {
  return Boolean(audioCtx) && audioCtx.state === "running" && !audio.paused;
}

function play() {
  wantPlay = true;
  ensureGraph();
  Promise.all([audioCtx.resume(), audio.play()]).then(() => {
    if (!wantPlay) {
      audio.pause();
      showPaused();
      return;
    }
    if (audioCtx.state === "running") {
      showPlaying();
      return;
    }
    audio.pause();
    wantPlay = false;
    showPaused();
    armUnlock();
  }).catch(() => {
    if (!wantPlay) {
      return;
    }
    showPaused();
    armUnlock();
  });
}

function pause() {
  wantPlay = false;
  audio.pause();
  showPaused();
}

function showMuted(muted) {
  muteButton.classList.toggle("is-muted", muted);
  muteButton.setAttribute("aria-pressed", muted ? "true" : "false");
  muteButton.setAttribute("aria-label", muted ? "Unmute" : "Mute");
  soundIcon.classList.toggle("hidden", muted);
  mutedIcon.classList.toggle("hidden", !muted);
}

function setVolume(amount) {
  const level = Math.min(100, Math.max(0, amount));
  volume.value = String(level);
  audio.volume = level / 100;
  volume.style.setProperty("--vol", level + "%");
  if (level > 0) {
    savedVolume = level;
  }
  showMuted(level === 0);
}

previousButton.addEventListener("click", () => {
  load(index - 1);
  play();
});

nextButton.addEventListener("click", () => {
  load(index + 1);
  play();
});

toggle.addEventListener("click", () => {
  if (audible()) {
    pause();
    return;
  }
  play();
});

muteButton.addEventListener("click", () => {
  if (Number(volume.value) > 0) {
    setVolume(0);
    return;
  }
  setVolume(savedVolume || 20);
});

volume.addEventListener("input", () => {
  setVolume(Number(volume.value));
});

audio.addEventListener("playing", () => {
  loadErrors = 0;
  if (wantPlay && audioCtx && audioCtx.state === "running") {
    showPlaying();
  }
});

audio.addEventListener("ended", () => {
  load(index + 1);
  play();
});

audio.addEventListener("error", () => {
  if (audio.error && audio.error.code === 1) {
    return;
  }
  loadErrors += 1;
  if (loadErrors > TRACKS.length) {
    wantPlay = false;
    showPaused();
    return;
  }
  load(index + 1);
  if (wantPlay) {
    play();
  }
});

window.addEventListener("resize", buildBars);

setVolume(Number(volume.value));
buildBars();
load(index);
play();
