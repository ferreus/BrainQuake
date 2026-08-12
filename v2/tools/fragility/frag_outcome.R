#!/usr/bin/env Rscript
# Li et al. 2021's surgical-outcome statistic, applied to one patient.
#
#   Rscript frag_outcome.R DATA_DIR --soz=A,I [--timing=timing.csv] [--ranked]
#
# The paper's classifier is a random forest over the 10 SOZ + 10 SOZC fragility
# quantiles; the trained model was never released, so what is reproduced here is
# its input -- the quantile matrix from fragStat -- plus the interpretability
# ratio I = F_SOZ(90th)/F_SOZC(90th) the paper defines over the same sets.
#
# The paper's reported contrast: in successful resections SOZ fragility sits far
# above SOZC (p=3.3e-70); in failures the two are indistinguishable (p=0.355).

suppressMessages(library(EZFragility))

args <- commandArgs(trailingOnly = TRUE)
if (!length(args)) stop("usage: Rscript frag_outcome.R DATA_DIR --soz=A,I")
DIR <- args[1]
rest <- args[-1]
flag <- function(n, d = character(0)) {
  h <- grep(paste0("^--", n, "="), rest, value = TRUE)
  if (!length(h)) return(d)
  strsplit(sub(paste0("^--", n, "="), "", h[1]), ",")[[1]]
}
SOZ_SHAFTS <- flag("soz")
if (!length(SOZ_SHAFTS)) stop("--soz=A,I is required (the clinically annotated onset shafts)")
RANKED <- any(rest == "--ranked")
TIMING <- flag("timing", file.path(dirname(DIR), "timing.csv"))[1]

shaft <- function(x) sub("^([A-Za-z]+'?)[0-9]+$", "\\1", x)

FULL <- file.path(DIR, "frag_full")
files <- Sys.glob(file.path(FULL, "*.rds"))
if (!length(files)) stop("no *.rds in ", FULL, " -- run frag_compute.R first")
labels <- sort(sub("\\.rds$", "", basename(files)))

# Per-seizure duration -> the paper's "first 5% of the seizure event".
dur <- list()
if (file.exists(TIMING)) {
  tm <- read.csv(TIMING, stringsAsFactors = FALSE)
  for (i in seq_len(nrow(tm))) {
    lab <- gsub(" ", "", tm$label[i])
    e <- suppressWarnings(as.numeric(tm$end[i]))
    if (!is.na(e)) dur[[lab]] <- e   # relative to the SZ mark (t = 0)
  }
}
med_dur <- if (length(dur)) median(unlist(dur)) else 60

# Windows to evaluate, in seconds relative to the export's t = 0 (the SZ nP mark).
win_for <- function(lab) {
  d <- if (!is.null(dur[[lab]])) dur[[lab]] else med_dur
  list(
    `paper [-10, 5% dur]` = c(-10, 0.05 * d),
    `pre-onset [-10, 0]`  = c(-10, 0),
    `early ictal [0, 3]`  = c(0, 3),
    `ictal [0, 5]`        = c(0, 5),
    `peri [-10, 3]`       = c(-10, 3)
  )
}

as_frag <- function(x) {
  methods::new("Fragility", ieegts = NULL, adj = NULL, frag = x$frag,
               frag_ranked = x$frag_ranked, R2 = x$R2, lambdas = x$lambdas,
               startTimes = x$startTimes, electrodes = x$electrodes)
}

cat(sprintf("SOZ = shafts {%s}   matrix = %s\n\n",
            paste(SOZ_SHAFTS, collapse = ", "), if (RANKED) "frag_ranked" else "frag"))

rows <- list()
soz_contacts <- NULL
for (lab in labels) {
  x <- readRDS(file.path(FULL, paste0(lab, ".rds")))
  el <- x$electrodes
  soz_idx <- which(shaft(el) %in% SOZ_SHAFTS)
  if (!length(soz_idx)) stop(lab, ": no contacts match --soz")
  if (is.null(soz_contacts)) soz_contacts <- el[soz_idx]

  fr <- as_frag(x)
  st <- x$startTimes
  fm <- if (RANKED) x$frag_ranked else x$frag

  for (wn in names(win_for(lab))) {
    w <- win_for(lab)[[wn]]
    sel <- which(st >= w[1] & st <= w[2])
    if (!length(sel)) next

    sub <- methods::new("Fragility", ieegts = NULL, adj = NULL,
                        frag = x$frag[, sel, drop = FALSE],
                        frag_ranked = x$frag_ranked[, sel, drop = FALSE],
                        R2 = x$R2[, sel, drop = FALSE], lambdas = x$lambdas[sel],
                        startTimes = st[sel], electrodes = el)
    fs <- fragStat(sub, groupIndex = soz_idx, ranked = RANKED)
    q <- fs$qmatrix
    # Time-averaged 90th-quantile of each set -> the paper's interpretability ratio.
    soz90 <- mean(q["SOZ90%", ])
    ref90 <- mean(q["REF90%", ])

    # One value per contact (mean over the window). Pooling contacts x windows
    # instead would inflate n ~200x and make every p vanish regardless of effect.
    cm <- rowMeans(fm[, sel, drop = FALSE])
    g <- cm[soz_idx]
    r <- cm[-soz_idx]
    pooled <- sqrt((var(g) * (length(g) - 1) + var(r) * (length(r) - 1)) /
                     (length(g) + length(r) - 2))
    rows[[length(rows) + 1]] <- data.frame(
      seizure = lab, window = wn,
      soz90 = soz90, ref90 = ref90, ratio = soz90 / ref90,
      soz_mean = mean(g), ref_mean = mean(r),
      # where the clinical SOZ sits in the implant-wide fragility distribution
      soz_pctile = 100 * mean(rank(cm)[soz_idx]) / length(cm),
      cohen_d = (mean(g) - mean(r)) / pooled,
      p = suppressWarnings(wilcox.test(g, r)$p.value),
      stringsAsFactors = FALSE)
  }
}
res <- do.call(rbind, rows)

cat(sprintf("SOZ contacts (%d): %s\n\n", length(soz_contacts),
            paste(soz_contacts, collapse = ", ")))

for (wn in unique(res$window)) {
  s <- res[res$window == wn, ]
  cat(sprintf("=== %s ===\n", wn))
  cat(sprintf("%-8s %8s %8s %8s %8s %9s %8s\n",
              "seizure", "SOZ90", "SOZC90", "ratio", "SOZ%ile", "cohen_d", "p"))
  for (i in seq_len(nrow(s))) {
    cat(sprintf("%-8s %8.4f %8.4f %8.3f %8.1f %9.2f %8.3f\n", s$seizure[i],
                s$soz90[i], s$ref90[i], s$ratio[i], s$soz_pctile[i],
                s$cohen_d[i], s$p[i]))
  }
  cat(sprintf("%-8s %8.4f %8.4f %8.3f %8.1f %9.2f\n\n", "MEAN",
              mean(s$soz90), mean(s$ref90), mean(s$ratio),
              mean(s$soz_pctile), mean(s$cohen_d)))
}

# Where the fragility actually is, regardless of the clinical annotation.
cat("=== most fragile shafts (paper window, size-normalised mean fragility) ===\n")
agg <- list()
for (lab in labels) {
  x <- readRDS(file.path(FULL, paste0(lab, ".rds")))
  fm <- if (RANKED) x$frag_ranked else x$frag
  w <- win_for(lab)[["paper [-10, 5% dur]"]]
  sel <- which(x$startTimes >= w[1] & x$startTimes <= w[2])
  sc <- rowMeans(fm[, sel, drop = FALSE])
  for (s in unique(shaft(x$electrodes))) {
    agg[[s]] <- c(agg[[s]], mean(sc[shaft(x$electrodes) == s]))
  }
}
m <- sort(sapply(agg, mean), decreasing = TRUE)
for (s in names(m)) {
  tag <- if (s %in% SOZ_SHAFTS) "  <<< clinical SOZ" else ""
  cat(sprintf("%6s %8.4f%s\n", s, m[[s]], tag))
}

out <- file.path(DIR, "outcome_stats.csv")
write.csv(res, out, row.names = FALSE)
cat("\nwrote", out, "\n")
