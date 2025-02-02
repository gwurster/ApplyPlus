import diff_match_patch as dmp_module
import scripts.patch_apply.patchParser as parse
import Levenshtein
from pygments.lexers import (
    CLexer,
    CppLexer,
    CSharpLexer,
    JavaLexer,
    get_lexer_for_filename,
)
from scripts.enums import Language, MatchStatus, natureOfChange

dmp = dmp_module.diff_match_patch()
LEVENSHTEIN_RATIO = 0.8

"""
The purpose of this variable is because (start of matched code + patch length)
does not always contain the entire patch, so our matched code is the section from
(start of matched code) to (start of matched code + patch length + PATCH_LENGTH_BUFFER)
"""
PATCH_LENGTH_BUFFER = 10


class Retry:
    def __init__(self, retry_times, retry_interval):
        self.retry_times = retry_times
        self.retry_interval = retry_interval


class Diff:
    class LineDiff:
        class LanguageSpecificDiff:
            lexer_to_language = {
                CLexer: Language.C,
                CppLexer: Language.CPP,
                CSharpLexer: Language.CSHARP,
                JavaLexer: Language.JAVA,
            }

            def __init__(
                self,
                language=Language.NOT_SUPPORTED,
                patch_tokens=[],
                file_tokens=[],
                diff_tokens=[],
            ):
                self.language = language
                self.patch_tokens = patch_tokens
                self.file_tokens = file_tokens
                self.diff_tokens = diff_tokens

        def __init__(
            self,
            patch_line,
            file_line="",
            is_missing=True,
            plaintext_diff=[],
            language_specific_diff=LanguageSpecificDiff(),
            match_ratio=-1,
            function_for_patch="",
            file_line_number=-1,
        ):

            self.patch_line = patch_line
            self.file_line = file_line
            self.is_missing = is_missing
            self.plaintext_diff = plaintext_diff
            self.language_specific_diff = language_specific_diff
            self.match_ratio = match_ratio
            self.function_for_patch = function_for_patch
            self.file_line_number = file_line_number

    def __init__(
        self,
        match_status,
        match_start_line=-1,
        removed_diffs=[],
        added_diffs=[],
        context_diffs=[],
        additional_lines=[],
        function_for_patch="",
    ):

        self.match_status = match_status
        self.match_start_line = match_start_line
        self.removed_diffs = removed_diffs
        self.added_diffs = added_diffs
        self.context_diffs = context_diffs
        self.additional_lines = additional_lines
        self.function_for_patch = function_for_patch


"""
See docs for output format:
https://github.com/google/diff-match-patch/wiki/API
"""


def calculate_plaintext_diff(patch_line, file_line):
    diff_tokens = dmp.diff_main(patch_line, file_line)
    dmp.diff_cleanupSemantic(diff_tokens)
    return diff_tokens


def calculate_language_diff(patch_line, file_line, file_name):
    try:
        lexer = get_lexer_for_filename(file_name)
        language = Diff.LineDiff.LanguageSpecificDiff.lexer_to_language[type(lexer)]
    except:
        language = Language.NOT_SUPPORTED

    if language == Language.NOT_SUPPORTED:
        return Diff.LineDiff.LanguageSpecificDiff()

    patch_tokens = []
    token_stream = lexer.get_tokens(patch_line)
    for token in token_stream:
        patch_tokens.append(token)

    file_tokens = []
    token_stream = lexer.get_tokens(file_line)
    for token in token_stream:
        file_tokens.append(token)

    diff_tokens = list(set(patch_tokens) - set(file_tokens))

    return Diff.LineDiff.LanguageSpecificDiff(
        language=language,
        patch_tokens=patch_tokens,
        file_tokens=file_tokens,
        diff_tokens=diff_tokens,
    )


# Returns line number of match location, returns -1 if no match
def fuzzy_search(search_lines, file_name, patch_line_number, retry_obj=None):
    search_pattern = "\n".join(search_lines)
    file_lines = []
    cur_char = 0
    search_location = -1
    cur_line = 1
    line_to_char_dict = {}

    with open(file_name) as f:
        for line in f:
            line_to_char_dict[cur_line] = cur_char
            cur_line += 1
            cur_char += len(line)
            file_lines.append(line)

        if patch_line_number in line_to_char_dict:
            search_location = line_to_char_dict[patch_line_number]
        else:
            search_location = cur_char

    file_str = "".join(file_lines)

    # Use retry_interval as inital interval, if not found, use default 1000 * 0.8 = 800
    best_threshold = 0.01
    high_threshold = 0.2
    default_distance = dmp.Match_Distance
    default_threshold = dmp.Match_Threshold
    distance = default_threshold * default_distance
    if retry_obj:
        end_line = retry_obj.retry_interval + patch_line_number
        if end_line in line_to_char_dict:
            distance = line_to_char_dict[end_line]

    # First look for a best similar match:
    dmp.Match_Threshold = best_threshold 
    dmp.Match_Distance = distance /best_threshold 
    char_match_loc = dmp.match_main(file_str, search_pattern, search_location)
    # Then look for a highly similar match:
    if char_match_loc == -1:
        dmp.Match_Threshold = high_threshold
        dmp.Match_Distance = distance / high_threshold
        char_match_loc = dmp.match_main(file_str, search_pattern, search_location)

    # no highly similar found, do a default fuzzy match
    if char_match_loc == -1:
        dmp.Match_Threshold = default_threshold
        dmp.Match_distance = default_distance
        char_match_loc = dmp.match_main(file_str, search_pattern, search_location)

    # no match found in the initial place. Retry:
    if char_match_loc == -1 and retry_obj:
        overlap_line = 5
        distance = retry_obj.retry_interval + overlap_line
        for i in range(1, retry_obj.retry_times + 1):
            above_start_line = patch_line_number - i * retry_obj.retry_interval
            below_start_line = patch_line_number + i * retry_obj.retry_interval - overlap_line
            search_above_res = -1
            search_below_res = -1
            if above_start_line not in line_to_char_dict and below_start_line not in line_to_char_dict:
                break

            # Search for a best similar match in both interval: 99%
            dmp.Match_Threshold = best_threshold 
            dmp.Match_Distance = distance / dmp.Match_Threshold
            if above_start_line in line_to_char_dict:
                search_above_res = dmp.match_main(
                    file_str, search_pattern, line_to_char_dict[above_start_line]
                )

            # Search the second interval:
            if below_start_line in line_to_char_dict:
                search_below_res = dmp.match_main(
                    file_str, search_pattern, line_to_char_dict[below_start_line]
                )
            
            if search_above_res == -1 and search_below_res == -1:
                # no best similar match, do highly similar match for 80%
                dmp.Match_Threshold = high_threshold 
                dmp.Match_Distance = distance / dmp.Match_Threshold
                if above_start_line in line_to_char_dict:
                    search_above_res = dmp.match_main(
                        file_str, search_pattern, line_to_char_dict[above_start_line]
                    )
                if below_start_line in line_to_char_dict:
                    search_below_res = dmp.match_main(
                        file_str, search_pattern, line_to_char_dict[below_start_line]
                    )
            elif search_above_res == -1 or search_below_res == -1:
                # we found exactly one highly similar match, return that one
                char_match_loc = search_above_res if search_above_res != -1 else search_below_res
                break

            if search_above_res == -1 and search_below_res == -1:
                # no highly similar match, do default threshold fuzzy match 50%
                dmp.Match_Threshold = default_threshold
                dmp.Match_Distance = distance / dmp.Match_Threshold
                if above_start_line in line_to_char_dict:
                    search_above_res = dmp.match_main(
                        file_str, search_pattern, line_to_char_dict[above_start_line]
                    )
                if below_start_line in line_to_char_dict:
                    search_below_res = dmp.match_main(
                        file_str, search_pattern, line_to_char_dict[below_start_line]
                    )
            elif search_above_res == -1 or search_below_res == -1:
                # we found exactly one highly similar match, return that one
                char_match_loc = search_above_res if search_above_res != -1 else search_below_res
                break

            if search_above_res != -1 and search_below_res != -1:
                # Found two highly similar match or two low similar match, calculate levenshtein ratio to decide
                above_ratio = Levenshtein.ratio(search_lines, file_str[search_above_res:(search_above_res+len(search_lines)) ] )
                below_ratio = Levenshtein.ratio(search_lines, file_str[search_below_res:(search_below_res+len(search_below_res)) ] )
                char_match_loc = search_above_res if above_ratio > below_ratio else search_below_res
                if above_ratio == below_ratio:
                    # if same ratio, return the closer one to the start point. If still same, we prefer the below one
                    char_match_loc = search_above_res if abs(search_location - search_above_res) < abs(search_below_res - search_location) else search_below_res 
                break
            elif search_above_res != -1 or search_below_res != -1:
                # we found exactly one low similar match, return that one
                char_match_loc = search_above_res if search_above_res != -1 else search_below_res
                break

            # We did not find any similar match in this interval. try again
            
    if char_match_loc != -1:
        return file_str[: char_match_loc + 1].count("\n") + 1
    else:
        return -1


def get_file_with_patch(patch_lines):
    search_lines = []
    for line in patch_lines:
        if line[0] != natureOfChange.REMOVED:
            search_lines.append(line)

    return search_lines


def get_file_without_patch(patch_lines):
    search_lines = []
    for line in patch_lines:
        if line[0] != natureOfChange.ADDED:
            search_lines.append(line)

    return search_lines


def is_already_moved(patch_idx, patch_lines, file_idx, file_lines):
    check_lines = []

    cur_patch_idx = patch_idx - 1

    while cur_patch_idx >= 0:
        if patch_lines[cur_patch_idx][0] != natureOfChange.ADDED:
            check_lines.append(patch_lines[cur_patch_idx][1].strip())
            if len(check_lines) == 2:
                break
        cur_patch_idx -= 1

    check_lines = check_lines[::-1]
    check_lines.append(patch_lines[patch_idx][1].strip())

    cur_patch_idx = patch_idx + 1
    next_non_removed = ""
    while cur_patch_idx < len(patch_lines):
        if patch_lines[cur_patch_idx][0] != natureOfChange.ADDED:
            check_lines.append(patch_lines[cur_patch_idx][1].strip())
            if len(check_lines) == 5:
                break
        cur_patch_idx += 1

    check_idx = 0
    for cur_file_idx in range(file_idx - 2, file_idx + 3):
        if cur_file_idx < 0 or cur_file_idx >= len(file_lines):
            continue
        if check_lines[check_idx] != file_lines[cur_file_idx].strip():
            return True
        check_idx += 1

    return False


def compare_nearby(patch_idx, patch_lines, file_idx, file_lines):
    above_res = True
    below_res = True

    cur_patch_idx = patch_idx - 1
    prev_non_removed = ""
    while cur_patch_idx >= 0:
        if patch_lines[cur_patch_idx][0] != natureOfChange.REMOVED:
            prev_non_removed = patch_lines[cur_patch_idx][1].strip()
            break
        cur_patch_idx -= 1

    cur_patch_idx = patch_idx + 1
    next_non_removed = ""
    while cur_patch_idx < len(patch_lines):
        if patch_lines[cur_patch_idx][0] != natureOfChange.REMOVED:
            next_non_removed = patch_lines[cur_patch_idx][1].strip()
            break
        cur_patch_idx += 1

    if file_idx != 0:
        if len(prev_non_removed) != 0:
            above_res = (
                Levenshtein.ratio(prev_non_removed, file_lines[file_idx - 1].strip())
                > LEVENSHTEIN_RATIO
            )
    if file_idx < len(file_lines) - 1:
        if len(next_non_removed) != 0:
            below_res = (
                Levenshtein.ratio(next_non_removed, file_lines[file_idx + 1].strip())
                > LEVENSHTEIN_RATIO
            )

    return above_res and below_res


# Returns an object containing information about the difference between a file and a patch
def find_diffs(patch_obj, file_name, retry_obj=None, match_distance=3000):
    dmp.Match_Distance = match_distance
    function_for_patch, patch_lines = patch_obj._lines[0][1], patch_obj._lines[1:]
    line_number = patch_obj._newStart

    search_lines_with_type = get_file_without_patch(patch_lines)
    search_lines_without_type = [line[1] for line in search_lines_with_type]

    match_start_line = fuzzy_search(
        search_lines_without_type, file_name, line_number, retry_obj
    )

    if match_start_line == -1:
        search_lines_with_type = get_file_with_patch(patch_lines)
        search_lines_without_type = [line[1] for line in search_lines_with_type]
        match_start_line = fuzzy_search(
            search_lines_without_type, file_name, line_number, retry_obj
        )

    if match_start_line == -1:
        return Diff(MatchStatus.NO_MATCH)

    with open(file_name) as f:
        file_lines = f.readlines()[
            match_start_line
            - 1 : match_start_line
            - 1
            + len(search_lines_with_type)
            + PATCH_LENGTH_BUFFER
        ]
    removed_diffs = []
    added_diffs = []
    context_diffs = []

    patch_line_type_to_list = {
        natureOfChange.ADDED: added_diffs,
        natureOfChange.REMOVED: removed_diffs,
        natureOfChange.CONTEXT: context_diffs,
    }

    added_lines = []
    for line in patch_lines:
        if line[0] == natureOfChange.ADDED:
            added_lines.append(line[1].strip())
    added_lines = set(added_lines)

    matched_file_lines = set()
    for idx, patch_line in enumerate(patch_lines):
        stripped_patch_line = patch_line[1].strip()
        if len(stripped_patch_line) == 0:
            continue
        max_ratio = 0
        max_ratio_file_line = ""
        matched_file_idx = -1
        # For each line in the patch, search over all lines in the file to find a match.
        for file_idx in range(len(file_lines)):
            file_line = file_lines[file_idx]
            cur_ratio = Levenshtein.ratio(file_line.strip(), stripped_patch_line)
            if cur_ratio >= max_ratio:
                if cur_ratio == max_ratio and not compare_nearby(
                    idx, patch_lines, file_idx, file_lines
                ):
                    continue
                max_ratio = cur_ratio
                max_ratio_file_line = file_line
                matched_file_idx = file_idx
        if max_ratio == 1 and patch_line[0] != natureOfChange.REMOVED:
            matched_file_lines.add(max_ratio_file_line.strip())
        elif max_ratio > LEVENSHTEIN_RATIO:
            # Attempt at trying to filter out moved lines
            if (
                patch_line[0] == natureOfChange.REMOVED
                and max_ratio_file_line.strip() in added_lines
                and is_already_moved(idx, patch_lines, matched_file_idx, file_lines)
            ):
                continue

            matched_file_lines.add(max_ratio_file_line.strip())

            plaintext_diff = calculate_plaintext_diff(
                stripped_patch_line, max_ratio_file_line.strip()
            )

            language_specific_diff = calculate_language_diff(
                stripped_patch_line, max_ratio_file_line.strip(), file_name
            )

            line_diff_obj = Diff.LineDiff(
                patch_line=stripped_patch_line,
                file_line=max_ratio_file_line,
                file_line_number=match_start_line + idx + 1,
                is_missing=False,
                plaintext_diff=plaintext_diff,
                language_specific_diff=language_specific_diff,
                match_ratio=max_ratio,
                function_for_patch=function_for_patch,
            )
            patch_line_type_to_list[patch_line[0]].append(line_diff_obj)
        elif patch_line[0] != natureOfChange.REMOVED:
            missing_diff = Diff.LineDiff(
                patch_line=stripped_patch_line,
                file_line=max_ratio_file_line,
                file_line_number=match_start_line + idx + 1,
                match_ratio=max_ratio,
                function_for_patch=function_for_patch,
                )
            patch_line_type_to_list[patch_line[0]].append(missing_diff)

    additional_lines = []
    matched_line_count = 0
    for line in file_lines:
        if len(line.strip()) == 0:
            continue
        if (
            line.strip() not in matched_file_lines
            and matched_line_count > 0
            and matched_line_count < len(matched_file_lines)
        ):
            additional_lines.append(line.strip())
        elif line.strip() in matched_file_lines:
            matched_line_count += 1

    return Diff(
        match_status=MatchStatus.MATCH_FOUND,
        match_start_line=match_start_line,
        removed_diffs=removed_diffs,
        added_diffs=added_diffs,
        context_diffs=context_diffs,
        additional_lines=additional_lines,
        function_for_patch=function_for_patch,
    )


# Testing
# patch_file = parse.PatchFile("../patches/CVE-2014-9322.patch")
# patch_file.getPatch()
# diff_obj = find_diffs(patch_file.patches[0], "../../msm-3.10/arch/x86/include/asm/page_32_types.h",
#     retry_obj=Retry(2,100), match_distance=3000)
# print(diff_obj.match_status)
# print(diff_obj.removed_diffs)
# print(diff_obj.added_diffs)
# print(diff_obj.context_diffs)
# print(diff_obj.additional_lines)
# for x in diff_obj.context_diffs:
#     print(x.function_for_patch)
#     print(x.file_line_number)
#     print(x.file_line)
