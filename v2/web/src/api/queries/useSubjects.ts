import { useQuery } from "@tanstack/react-query";
import { createSubject, deleteSubject, listSubjects } from "../endpoints";
import { qk } from "../queryKeys";
import { makeMutation } from "./factories";
import type { ReconType } from "../types";

export function useSubjects() {
  return useQuery({ queryKey: qk.subjects(), queryFn: listSubjects });
}

export const useCreateSubject = makeMutation(
  ({ name, reconType }: { name: string; reconType?: ReconType }) => createSubject(name, reconType),
  [qk.subjects()],
);

export const useDeleteSubject = makeMutation((id: number) => deleteSubject(id), [qk.subjects(), qk.jobs()]);
