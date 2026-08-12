#!/usr/bin/env Rscript
# Time-resolved neural fragility for every exported seizure, persisted whole.
#
# run_frag.R collapses fragility to one score per contact, which throws away the
# time axis that Li et al.'s outcome statistic needs. This keeps the full
# electrodes x windows matrix.
#
#   Rscript frag_compute.R DATA_DIR [LABEL ...] [--parallel[=N]]
#
# Writes DATA_DIR/frag_full/{label}.rds incrementally -- an interrupted run
# resumes rather than restarting.

suppressMessages(library(EZFragility))
suppressMessages(library(Epoch))

FS   <- 1000  # Hz; must match the export
PRE  <- 20    # s before onset in the exported window -- sets t = 0 at the onset
WIN  <- 250   # samples per fragility window (250 ms at 1 kHz), per Li et al.
STEP <- 125   # samples between windows (125 ms), per Li et al.

args <- commandArgs(trailingOnly = TRUE)
if (!length(args)) stop("usage: Rscript frag_compute.R DATA_DIR [LABEL ...]")
DIR <- args[1]
rest <- args[-1]

# calcAdjFrag fans out over windows with %dopar%; without a registered backend
# that silently falls back to sequential, so register one here.
par_arg <- grep("^--parallel", rest, value = TRUE)
PARALLEL <- length(par_arg) > 0
if (PARALLEL) {
  n <- sub("^--parallel=?", "", par_arg[1])
  nw <- if (nzchar(n)) as.integer(n) else max(1L, parallel::detectCores() - 2L)
  suppressMessages(library(doParallel))
  cl <- parallel::makeCluster(nw)
  registerDoParallel(cl)
  on.exit(parallel::stopCluster(cl), add = TRUE)
  cat(sprintf("parallel: %d workers\n", nw))
}

szs <- rest[!grepl("^--", rest)]
if (!length(szs)) {
  szs <- sort(sub("_ch\\.txt$", "", basename(Sys.glob(file.path(DIR, "*_ch.txt")))))
}
if (!length(szs)) stop("no *_ch.txt found in ", DIR)

OUT <- file.path(DIR, "frag_full")
dir.create(OUT, showWarnings = FALSE, recursive = TRUE)

for (sz in szs) {
  dest <- file.path(OUT, paste0(sz, ".rds"))
  if (file.exists(dest)) {
    cat(sprintf("%s: cached\n", sz)); next
  }

  tbl <- as.matrix(read.csv(file.path(DIR, paste0(sz, ".csv")), header = FALSE))
  ch  <- readLines(file.path(DIR, paste0(sz, "_ch.txt")))
  if (nrow(tbl) != length(ch)) {
    stop(sprintf("%s: %d CSV rows but %d channel names", sz, nrow(tbl), length(ch)))
  }
  rownames(tbl) <- ch
  ep <- Epoch(tbl, times = (seq_len(ncol(tbl)) - 1) / FS - PRE, samplingRate = FS)

  t0 <- Sys.time()
  fr <- calcAdjFrag(epoch = ep, window = WIN, step = STEP, progress = FALSE,
                    parallel = PARALLEL)
  mins <- as.numeric(difftime(Sys.time(), t0, units = "mins"))

  # Drop the adj array (184x184x239 doubles/seizure); nothing downstream reads it.
  saveRDS(list(frag = fr$frag, frag_ranked = fr$frag_ranked, R2 = fr$R2,
               startTimes = fr$startTimes, electrodes = fr$electrodes,
               lambdas = fr$lambdas, window = WIN, step = STEP, fs = FS),
          dest)

  cat(sprintf("%s: %d electrodes x %d windows, R2 median %.3f, frag range [%.3f, %.3f], %.1f min\n",
              sz, nrow(fr$frag), ncol(fr$frag), median(fr$R2),
              min(fr$frag), max(fr$frag), mins))
  flush.console()
}

cat("done ->", OUT, "\n")
