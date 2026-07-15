"use client";

import { useEffect, useState } from "react";
import { ApiError, getJob } from "./api";
import { isJobLive } from "./types";
import type { JobRecord } from "./types";

/** Slow enough not to hammer a server that is busy running the model, fast
 *  enough that a finished job appears to land the moment it lands. */
const POLL_MS = 2000;

interface JobState {
  job: JobRecord | null;
  /** Work is in flight. True from the moment a job id is handed over, before
   *  the first poll answers — otherwise the UI flashes "idle" for one tick and
   *  re-enables the button that was just pressed. */
  isLive: boolean;
}

/**
 * Follow a background job to its end.
 *
 * Polls `GET /jobs/{id}` every {@link POLL_MS} and stops for good once the job
 * succeeds or fails — a terminal job never changes again, so continuing to ask
 * is pure noise. Pass `null` to follow nothing.
 *
 * Transient failures are ignored on purpose: a dropped poll during a run that
 * takes minutes is not worth surfacing, and the next tick retries. A 404 is
 * different — the server does not know this job, so no amount of asking will
 * help, and polling stops.
 */
export function useJob(jobId: string | null): JobState {
  const [state, setState] = useState<JobState>(() => ({
    job: null,
    isLive: jobId !== null,
  }));

  // Reset during render rather than in an effect: the alternative paints one
  // frame of the *previous* job's record under the new id, which reads as the
  // old brief being finished when a new run has just started.
  const [followed, setFollowed] = useState(jobId);
  if (followed !== jobId) {
    setFollowed(jobId);
    setState({ job: null, isLive: jobId !== null });
  }

  useEffect(() => {
    if (jobId === null) return;

    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };

    const poll = () => {
      getJob(jobId)
        .then((job) => {
          if (cancelled) return;
          setState({ job, isLive: isJobLive(job) });
          if (!isJobLive(job)) stop();
        })
        .catch((err) => {
          if (cancelled) return;
          if (err instanceof ApiError && err.status === 404) {
            setState({ job: null, isLive: false });
            stop();
          }
          // Anything else: keep polling. The run outlives a blip.
        });
    };

    poll();
    timer = setInterval(poll, POLL_MS);

    // Covers unmount *and* a change of job id — the interval for the old id is
    // always cleared before the new one starts, so they cannot stack up.
    return () => {
      cancelled = true;
      stop();
    };
  }, [jobId]);

  return state;
}

/**
 * Seconds a job has been running, measured from the server's `started_at` (or
 * `created_at` while it is still queued) rather than from a local stopwatch —
 * so a job picked up after a reload shows the time it has actually been
 * running, not the time since this tab happened to open.
 */
export function useJobElapsed(job: JobRecord | null, isLive: boolean): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!isLive) return;
    const tick = () => setNow(Date.now());
    const t = setInterval(tick, 1000);
    // Background tabs throttle timers to roughly once a minute. Catch up when
    // the tab comes back rather than showing a clock that quietly stopped.
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [isLive]);

  const start = job?.started_at ?? job?.created_at ?? null;
  if (!start) return 0;
  const ms = now - Date.parse(start);
  // Clamped: the two clocks are not the same clock, and a job that appears to
  // have started in the future should read 0:00, not a negative number.
  return Number.isFinite(ms) ? Math.max(0, Math.floor(ms / 1000)) : 0;
}
