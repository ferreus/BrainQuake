#!/usr/bin/env Rscript
# Helper script to run EZEI on a single run data matrix or batch

.libPaths(c('~/R/library', .libPaths()))

# Source EZEI R files
ezei_dir <- '/home/ferreus/dev/EZEI/R'
for (f in list.files(ezei_dir, pattern = '\\.[rR]$', full.names = TRUE)) {
    source(f)
}

# Patch computeEpileptogenicIndex to prevent nwt dimension mismatch bug on arbitrary fs
computeEpileptogenicIndexFixed <- function(epoch, windowParams, fs=1000){
  thetaBand<-c(3.5,7.4)
  alphaBand<-c(7.4,12.4)
  betaBand<-c(12.4,24)
  gammaBand<-c(24,140)

  timeSeries<-tblData(epoch)
  v=0.5
  lambda=15

  timeNum <- ncol(timeSeries)
  elecNum <- nrow(timeSeries)

  rangeBand<-c(3.5,140)
  data   <- vector(mode="numeric", length=timeNum)
  data[1:timeNum]<-timeSeries[1,1:timeNum]

  # Compute the multitaper spectrogram
  results = multitaperSpectrogramR(data=data, fs=fs, windowParams = windowParams, frequencyRange=rangeBand)
  stimes = results[[2]]
  nwt = length(stimes)

  times<-as.numeric(colnames(timeSeries))
  timesOnset<-results[[2]]+times[1]

  ermaster=matrix(0,elecNum,nwt)
  unmaster=matrix(0,elecNum,nwt)

  for(ie in 1:elecNum){
    data[1:timeNum]<-timeSeries[ie,1:timeNum]
    results = multitaperSpectrogramR(data, fs, windowParams = windowParams, frequencyRange=rangeBand)
    spec=results[[1]]
    sfreq=results[[3]]

    eralpha=computeMeanPowBand(results[[1]],alphaBand,sfreq)
    ertheta=computeMeanPowBand(results[[1]],thetaBand,sfreq)
    erbeta=computeMeanPowBand(results[[1]],betaBand,sfreq)
    ergamma=computeMeanPowBand(results[[1]],gammaBand,sfreq)

    er=(erbeta+ergamma)/(eralpha+ertheta)
    # Ensure er matches nwt
    if (length(er) > nwt) er <- er[1:nwt]
    if (length(er) < nwt) er <- c(er, rep(er[length(er)], nwt - length(er)))

    ern=cumsum(er)/c(1:nwt)
    un=er-ern-v
    un=cumsum(un)

    ermaster[ie,]=er
    unmaster[ie,]=un
  }

  stimes = results[[2]]
  if (length(stimes) > nwt) stimes <- stimes[1:nwt]

  Nd   <- vector(mode="numeric", length=elecNum)
  Na   <- vector(mode="numeric", length=elecNum)

  Nd[1:elecNum]=10*nwt
  Na[1:elecNum]=nwt

  for(it in 2:nwt){
    unt=unmaster[,1:it, drop=FALSE]
    un=apply(unt,1,FUN=min)
    ind=apply(unt,1,which.min)

    undiff=unmaster[,it]-un
    pastThreshold=undiff>lambda
    pastThreshold[Nd!=10*nwt]=FALSE

    ie=which(pastThreshold==TRUE)
    Nd[ie]=ind[ie]
    Na[ie]=it
  }

  tau=1
  H=5

  hspan=which.min(abs(stimes-H))
  if (length(hspan) == 0 || is.na(hspan)) hspan <- 1

  maxNd<-nwt-hspan
  Nd[is.na(Nd)]=maxNd
  Nd[Nd>nwt-hspan]=maxNd

  EI<- vector(mode="numeric", length=elecNum)
  N0=min(Nd)
  t0=stimes[N0]+times[1]

  for(ie in 1:elecNum){
    idx_end <- min(nwt, Nd[ie] + hspan)
    denom <- stimes[max(1, Nd[ie]-t0+tau)]
    if (is.na(denom) || denom == 0) denom <- 1.0
    EI[ie]=mean(ermaster[ie, Nd[ie]:idx_end]) / denom
  }

  maxei=max(EI)
  if (maxei > 0) EI=EI/maxei

  idNoDetect<-which(Nd==maxNd)
  td<-stimes[Nd]+times[1]
  td[idNoDetect]<-times[timeNum]

  EpileptogenicIndex(
    energyRatio= ermaster,
    epileptogenicIndex=EI,
    timeDetect=td,
    startTimes = timesOnset,
    electrodes = rownames(timeSeries)
  )
}

if (!exists('tblData')) {
    tblData <- function(x) {
        if (is.matrix(x)) return(x)
        return(x)
    }
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
    cat("Usage: Rscript run_ezei_batch.R <data_csv> <fs> <output_json>\n")
    q(status = 1)
}

data_csv <- args[1]
fs <- as.numeric(args[2])
output_json <- args[3]

if (!file.exists(data_csv)) {
    cat("Error: data CSV not found:", data_csv, "\n")
    q(status = 1)
}

# Read CSV (first column: channel_name, remaining columns: samples)
df <- read.csv(data_csv, check.names = FALSE, header = FALSE, stringsAsFactors = FALSE)
ch_names <- df[, 1]
mat <- as.matrix(df[, -1])
storage.mode(mat) <- "numeric"
rownames(mat) <- ch_names
colnames(mat) <- seq(0, (ncol(mat) - 1) / fs, length.out = ncol(mat))

# Run EZEI using fixed function
windowParams <- c(0.25, 0.1)
res <- tryCatch({
    computeEpileptogenicIndexFixed(epoch = mat, windowParams = windowParams, fs = fs)
}, error = function(e) {
    cat("EZEI compute error:", e$message, "\n")
    NULL
})

if (is.null(res)) {
    cat("Failed to compute EZEI\n")
    q(status = 1)
}

ei_scores <- res@epileptogenicIndex
names(ei_scores) <- ch_names

# Output CSV
out_df <- data.frame(channel = ch_names, score = ei_scores, stringsAsFactors = FALSE)
write.csv(out_df, file = output_json, row.names = FALSE)
cat("SUCCESS\n")
