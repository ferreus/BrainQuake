#!/usr/bin/env Rscript
# Neural fragility (Li et al. 2021, doi:10.1038/s41593-021-00901-w) via the
# EZFragility package, over the seizure windows written by export_edf.py.
#
# Usage:
#   Rscript run_frag.R DATA_DIR [LABEL ...]
#   Rscript run_frag.R DATA_DIR --onset-shafts=A,I --spread-shafts=N,P,G
#
# With no labels, every {label}_ch.txt in DATA_DIR is processed. Results are
# written to DATA_DIR/frag_scores.rds as a named list of per-contact scores.

suppressMessages(library(EZFragility))
suppressMessages(library(Epoch))

FS   <- 1000   # Hz; must match the recording export_edf.py read
PRE  <- 20     # s before onset in the exported window -- sets t = 0 at the onset
WIN  <- 250    # samples per fragility window (250 ms at 1 kHz)
STEP <- 125    # samples between windows
ICTAL_END <- 5 # s after onset; scores average the windows in [0, ICTAL_END]
TOP_N <- 20    # contacts per seizure that vote for their shaft

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("usage: Rscript run_frag.R DATA_DIR [LABEL ...] [--onset-shafts=A,I]")

DIR <- args[1]
rest <- args[-1]
flag <- function(name, default = character(0)) {
  hit <- grep(paste0("^--", name, "="), rest, value = TRUE)
  if (!length(hit)) return(default)
  strsplit(sub(paste0("^--", name, "="), "", hit[1]), ",")[[1]]
}
GT_ON <- flag("onset-shafts")
GT_SP <- flag("spread-shafts")

szs <- rest[!grepl("^--", rest)]
if (!length(szs)) {
  szs <- sort(sub("_ch\\.txt$", "", basename(Sys.glob(file.path(DIR, "*_ch.txt")))))
}
if (!length(szs)) stop("no *_ch.txt found in ", DIR)

allres <- list()
for (sz in szs) {
  tbl <- as.matrix(read.csv(file.path(DIR, paste0(sz, ".csv")), header = FALSE))
  ch  <- readLines(file.path(DIR, paste0(sz, "_ch.txt")))
  if (nrow(tbl) != length(ch)) {
    stop(sprintf("%s: %d CSV rows but %d channel names", sz, nrow(tbl), length(ch)))
  }
  rownames(tbl) <- ch
  ep <- Epoch(tbl, times = (seq_len(ncol(tbl)) - 1) / FS - PRE, samplingRate = FS)

  t0 <- Sys.time()
  fr <- calcAdjFrag(epoch = ep, window = WIN, step = STEP, progress = FALSE)
  el <- fr$electrodes
  fm <- fr$frag
  st <- fr$startTimes

  sel <- which(st >= 0 & st <= ICTAL_END)
  if (!length(sel)) stop(sprintf("%s: no window starts within [0, %g]s of the onset", sz, ICTAL_END))
  score <- rowMeans(fm[, sel, drop = FALSE])
  names(score) <- el
  allres[[sz]] <- score

  # R2 is the fit quality of the per-window linear model everything downstream
  # rests on; a low median means the ranking is describing noise.
  cat(sprintf("%s: %d electrodes x %d windows, R2 median %.3f, %.0fs\n",
              sz, nrow(fm), ncol(fm), median(fr$R2), as.numeric(difftime(Sys.time(), t0, units = "secs"))))
  cat("   top 10: ", paste(names(sort(score, decreasing = TRUE))[1:10], collapse = ", "), "\n")
}

saveRDS(allres, file.path(DIR, "frag_scores.rds"))

# --- aggregate to shafts, normalised for contact count -------------------
# NB: the replacement must be "\\1". R reads "\1" as the octal escape for
# character 1, not as a regex backreference, which silently collapses every
# contact into one bucket -- see README.md, "A bug worth knowing about".
shaft <- function(x) sub("^([A-Za-z]+'?)[0-9]+$", "\\1", x)

sizes <- table(shaft(names(allres[[1]])))
cnt <- setNames(integer(length(sizes)), names(sizes))
for (sz in szs) {
  top <- names(sort(allres[[sz]], decreasing = TRUE))[seq_len(min(TOP_N, length(allres[[sz]])))]
  tally <- table(factor(shaft(top), levels = names(sizes)))
  cnt <- cnt + as.integer(tally)
}
norm <- cnt / as.numeric(sizes[names(cnt)])
ord <- order(-norm)

cat(sprintf("\n=== NEURAL FRAGILITY: shaft ranking (top-%d contacts per seizure, size-normalised) ===\n", TOP_N))
cat(sprintf("%6s %7s %7s %11s   %s\n", "shaft", "n", "votes", "votes/ch", "clinical"))
for (i in head(ord, 12)) {
  s <- names(sizes)[i]
  tag <- if (s %in% GT_ON) "<<< EEG ONSET" else if (s %in% GT_SP) "early spread" else ""
  cat(sprintf("%6s %7d %7d %11.2f   %s\n", s, sizes[[s]], cnt[i], norm[i], tag))
}
