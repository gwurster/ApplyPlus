import argparse

import patchParser as parse
# import check_file_exists_elsewhere as fileCheck
import test_match as tm
import os
import time
import context_changes as cc
import check_file_exists_elsewhere as check_exist

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reverse", help="Apply a patch in reverse files and/or to the index.", action='store_true')

    parser.add_argument(
        'pathToPatch', help="Path to the patch that needs to be applied.")

    args = parser.parse_args()
    return args

def apply_reverse(pathToPatch):
    print ("Apply in reverse called")

def apply(pathToPatch):
    patch_file = parse.PatchFile(pathToPatch)
    patch_file.runPatch()
    if(patch_file.runSuccess == True):
        print("Successfully applied")
        return 0
    else:
        print("Patch failed while it was run with git apply with error:")
        error_message = patch_file.runResult.decode('utf-8')
        print(error_message)

        error_message_lines = error_message.split('\n')        
        already_exists = set()
        file_not_found = set()
        does_not_apply = set()

        for line in error_message_lines:
            split_line = line.split(":")
            if "patch does not apply" in line:
                does_not_apply.add(split_line[1].strip())
            elif "already exists" in line:
                already_exists.add(split_line[1].strip())
            elif "No such file" in line:
                file_not_found.add(split_line[1].strip())
            
        patch_file.getPatch()
        
        # TODO: Handle file that already exists
        
        # Handling sub patches do not apply
        # try_all_subpatches_input = input("Would you like to try and apply all subpatches? [Y/n] ")
        try_all_subpatches = True
        successful_subpatches = []
        already_applied_subpatches = []
        failed_subpatches_with_matched_code = []
        subpatches_without_matched_code = []
        # not_tried_subpatches = []
        see_patches = input("We have found {} subpatches in the patch file. Would you like to see them? [Y/n] ".format(len(patch_file.patches)))
        see_patches = (see_patches.upper() == "Y")
        for patch in patch_file.patches:
            fileName = patch._fileName[1:]
            if see_patches:
                print(":".join([fileName, str(-patch._lineschanged[0])]))
                print(patch)
                print("----------------------------------------------------------------------")
            if fileName in file_not_found:
                correct_loc = check_exist.checkFileExistsElsewhere(patch)
                if correct_loc != None:
                    does_not_apply.add(correct_loc)
                    file_not_found.remove(fileName)
                    fileName = correct_loc
                    patch._fileName = "/" + correct_loc
            if fileName in does_not_apply:
                # [1:] is used to remove the leading slash
                subpatch_name =  ":".join([fileName, str(-patch._lineschanged[0])])

                # skip_current_patch = True 
                # if not try_all_subpatches:
                #     print("\nFailed Subpatch : {}\n".format(subpatch_name))
                    
                #     print(patch)
                #     skip_current_patch = (input("\nWould you like to try apply this subpatch? [Y/n] ").upper() != "Y")
                
                # if not try_all_subpatches and skip_current_patch:
                #     not_tried_subpatches.append(subpatch_name)
                #     continue

                # Try applying the subpatch as normal
                subpatch_run_success = patch.canApply(fileName)
                if subpatch_run_success:
                    successful_subpatches.append([patch, subpatch_name])
                else:
                    context_change_obj = cc.context_changes(patch)
                    diff_obj = context_change_obj.diff_obj
                    context_decision = context_change_obj.status
                    context_decision_msg = context_change_obj.messages
                    
                    if diff_obj.match_status == tm.Diff.MatchStatus.MATCH_FOUND:
                        added_line_count = 0
                        removed_line_count = 0
                        patch_lines = patch.getLines()

                        for line in patch_lines:
                            if line[0] == parse.natureOfChange.ADDED:
                                added_line_count += 1
                            elif line[0] == parse.natureOfChange.REMOVED:
                                removed_line_count += 1

                        applied_percentage = 100 - (len(diff_obj.added_diffs) + len(diff_obj.removed_diffs))/(added_line_count+removed_line_count)*100

                        # Exact Patch Has Already Been Applied
                        if len(diff_obj.context_diffs) == 0 and len(diff_obj.added_diffs) == 0 and len(diff_obj.removed_diffs) == 0 and len(diff_obj.additional_lines) == 0:
                            already_applied_subpatches.append(subpatch_name)

                        # No lines between the context lines other than parts of the patch (currently only case where we can apply patches)
                        elif len(diff_obj.additional_lines) == 0: 
                            # We should not apply the patch, context changes affect the code
                            if context_decision == cc.CONTEXT_DECISION.DONT_RUN:
                                failed_subpatches_with_matched_code.append((applied_percentage, subpatch_name, diff_obj.match_start_line))
                                
                            # We try to apply the patch, context changes are not important
                            # The case below is only for cases where the context is the only thing that is changed or a line has been completely added or removed with no similar lines
                            else:
                                new_patch_lines = [patch_lines[0]]
                                context_diff_index = 0
                                added_diff_index = 0
                                removed_diff_index = 0
                                for line in patch_lines[1:]:
                                    if line[0] == parse.natureOfChange.CONTEXT:
                                        if context_diff_index < len(diff_obj.context_diffs) and diff_obj.context_diffs[context_diff_index].patch_line.strip() == line[1].strip():
                                            if diff_obj.context_diffs[context_diff_index].is_missing:
                                                context_diff_index += 1
                                                continue
                                            new_context_line = diff_obj.context_diffs[context_diff_index].file_line
                                            new_patch_lines.append((parse.natureOfChange.CONTEXT, new_context_line))
                                            context_diff_index += 1
                                        else:
                                            new_patch_lines.append(line)
                                    elif line[0] == parse.natureOfChange.ADDED:
                                        new_patch_lines.append(line)
                                        # if added_diff_index < len(diff_obj.added_diffs) and diff_obj.added_diffs[added_diff_index].patch_line.strip() == line[1].strip():
                                        #     new_patch_lines.append(line)
                                        #     added_diff_index += 1
                                        # else:
                                        #     new_patch_lines.append((parse.natureOfChange.CONTEXT, line[1]))
                                    elif line[0] == parse.natureOfChange.REMOVED:
                                        if removed_diff_index < len(diff_obj.removed_diffs) and diff_obj.removed_diffs[removed_diff_index].patch_line.strip() == line[1].strip():
                                            new_patch_lines.append(line)
                                            removed_diff_index += 1

                                old_patch_lines = patch._lines
                                patch._lines = new_patch_lines
                                if patch.canApply(fileName):
                                    successful_subpatches.append([patch, subpatch_name])
                                else:
                                    # print("Issue with current assumption in terms of what patches can be applied")
                                    failed_subpatches_with_matched_code.append((applied_percentage, subpatch_name, diff_obj.match_start_line))
                                    
                        else:
                            failed_subpatches_with_matched_code.append((applied_percentage, subpatch_name, diff_obj.match_start_line))

                    else:
                        subpatches_without_matched_code.append(subpatch_name)
    
  
        print("----------------------------------------------------------------------")
        print("----------------------------------------------------------------------")
        if len(successful_subpatches) > 0:
            print("{} subpatches can be applied successfully:\n".format(len(successful_subpatches)))
            start_apply= input("Would you like to see these patches and try applying them? [Y/n] ")
            start_apply = (start_apply.upper() == "Y")
            if(start_apply):
                for patch in successful_subpatches:
                    print(patch[1])
                    print(patch[0])
                    print("----------------------------------------------------------------------\n")
                    apply_subpatch_input = input("The above subpatch can be applied successfully. Would you like to apply? [Y/n] ")
                    apply_subpatch_input = (apply_subpatch_input.upper() == "Y")
                    success = False
                    if apply_subpatch_input:
                        fileName = patch[0]._fileName[1:]
                        patchObj = patch[0]
                        success = patchObj.Apply(fileName)
                    if (success):
                        print("Successfully applied!")
                    else:
                        print("Ignored")
                    time.sleep(1)
                    print("----------------------------------------------------------------------\n")
                    
        if len(failed_subpatches_with_matched_code) > 0:
            failed_subpatches_with_matched_code.sort()
            print("Subpatches that we can't automatically apply, but think we have found where the patch should be applied:")

            for applied_percentage, sp_name, line_number in failed_subpatches_with_matched_code:
                print("{} - Line Number: {} - Applied Percentage: {}%".format(sp_name, line_number, applied_percentage))
            print("\nNote that if a subpatch has an Applied Percentage of 100%, that means that the context may have changed in ways that affect the code")
            print("----------------------------------------------------------------------")

        if len(already_applied_subpatches) > 0:
            print("Subpatches that were already applied:")
            print("\n".join(already_applied_subpatches))
            print("----------------------------------------------------------------------")

        if len(subpatches_without_matched_code) > 0:
            print("Subpatches that did not apply, and we could not find where the patch should be applied:")
            print("\n".join(subpatches_without_matched_code))
            print("----------------------------------------------------------------------")
        
        if len(file_not_found) > 0:
            print("The following files could not be found:")
            print("\n".join(file_not_found))
            print("----------------------------------------------------------------------")
        # if len(not_tried_subpatches) > 0:
        #     print("\nSubpatches that we did not try and apply:")
        #     print("\n".join(not_tried_subpatches))

        return 1


def main():
    if (args.reverse):
        apply_reverse(args.pathToPatch)
    else:
        apply(args.pathToPatch)

if __name__ == "__main__":
    args = get_args()
    main()
