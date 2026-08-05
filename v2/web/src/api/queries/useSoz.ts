import { fuseSoz, getSozResult } from "../endpoints";
import { qk } from "../queryKeys";
import { makeSubjectMutation, makeSubjectQuery } from "./factories";
import type { SozFuseParams } from "../endpoints";
import type { Job } from "../types";

export const useFuseSoz = makeSubjectMutation<SozFuseParams, Job>(fuseSoz, () => [qk.jobs()]);

export const useSozResult = makeSubjectQuery(getSozResult, qk.sozResult, { retry: false });
