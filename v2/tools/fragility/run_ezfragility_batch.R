#!/usr/bin/env Rscript
# Runner script for EZFragility R package (Li et al. 2021) on single seizure slice

suppressMessages(library(EZFragility))
suppressMessages(library(Epoch))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 5) {
  cat("Usage: Rscript run_ezfragility_batch.R <data_csv> <fs> <pre_s> <score_s> <output_csv>\n")
  q(status = 1)
}

data_csv  <- args[1]
fs        <- as.numeric(args[2])
pre_s     <- as.numeric(args[3])
score_s   <- as.numeric(args[4])
out_csv   <- args[5]

if (!file.exists(data_csv)) {
  cat("Error: CSV input not found:", data_csv, "\n")
  q(status = 1)
}

t0 <- Sys.time()

# Read CSV (first column: channel_name, rest: samples)
df <- read.csv(data_csv, header = FALSE, check.names = FALSE, stringsAsFactors = FALSE)
ch_names <- df[, 1]
mat <- as.matrix(df[, -1])
storage.mode(mat) <- "numeric"
rownames(mat) <- ch_names

# Construct Epoch object (t = 0 at onset)
times <- (seq_len(ncol(mat)) - 1) / fs - pre_s
ep <- Epoch(mat, times = times, samplingRate = fs)

# EZFragility parameters (250 ms window, 125 ms step)
win_samples  <- as.integer(round(0.25 * fs))
step_samples <- as.integer(round(0.125 * fs))

res <- tryCatch({
  calcAdjFrag(epoch = ep, window = win_samples, step = step_samples, progress = FALSE)
}, error = function(e) {
  cat("calcAdjFrag error:", e$message, "\n")
  NULL
})

if (is.null(res)) {
  cat("Error: EZFragility execution failed\n")
  q(status = 1)
}

elapsed_s <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
fm <- res$frag
st <- res$startTimes
el <- res$electrodes
r2_med <- median(res$R2, na.rm = TRUE)

# Average scores over ictal evaluation window [0, score_s]
sel <- which(st >= 0.0 & st <= score_s)
if (length(sel) == 0) {
  # Fallback to all windows if no windows strictly inside [0, score_s]
  sel <- seq_len(ncol(fm))
}

scores <- rowMeans(fm[, sel, drop = FALSE], na.rm = TRUE)
names(scores) <- el

out_df <- data.frame(
  channel = el,
  score = as.numeric(scores),
  r2_median = r2_med,
  elapsed_sec = elapsed_s,
  stringsAsFactors = FALSE
)

write.csv(out_df, file = out_csv, row.names = FALSE)
cat(sprintf("SUCCESS: %d channels, R2 median %.3f in %.2fs\n", length(el), r2_med, elapsed_s))
