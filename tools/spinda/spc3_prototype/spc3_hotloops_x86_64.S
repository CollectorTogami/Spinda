# SPDX-License-Identifier: MPL-2.0
.intel_syntax noprefix
.text

# COLLECTOR_TOGAMI_SOURCE_CREDIT_2026-05-11
# Project credit: Collector Togami is the person behind the Spinda/SPC3 project as a whole and is credited as project originator, coordinator, and driving force.
#

# Credit: Shawrkie helped with SPC3 compressor/decompressor work and
# contributed compute for corpus processing and verification. Keep this credit
# with source and binary packages that include the SPC3 toolchain.
#
# Windows x64 ABI:
#   arg1 = rcx, arg2 = rdx, arg3 = r8, arg4 = r9
# These are leaf functions and only touch volatile registers.

.globl spc3_xor48_asm
spc3_xor48_asm:
    mov eax, edx
    lea rcx, [rcx + 0x20]
    mov r8d, 12
.Lxor48_loop:
    xor DWORD PTR [rcx], eax
    add rcx, 4
    dec r8d
    jne .Lxor48_loop
    ret

.globl spc3_shuffle48_asm
spc3_shuffle48_asm:
    # rcx = full 80-byte PK3 record, rdx = four-byte block order.
    # Copy the 48-byte encrypted/decrypted data region through stack scratch so
    # overlapping block moves are safe without calling memcpy.
    sub rsp, 56

    movzx r8d, BYTE PTR [rdx]
    lea r9, [r8 + r8 * 2]
    shl r9, 2
    mov r10, QWORD PTR [rcx + 0x20 + r9]
    mov QWORD PTR [rsp], r10
    mov r10d, DWORD PTR [rcx + 0x28 + r9]
    mov DWORD PTR [rsp + 8], r10d

    movzx r8d, BYTE PTR [rdx + 1]
    lea r9, [r8 + r8 * 2]
    shl r9, 2
    mov r10, QWORD PTR [rcx + 0x20 + r9]
    mov QWORD PTR [rsp + 12], r10
    mov r10d, DWORD PTR [rcx + 0x28 + r9]
    mov DWORD PTR [rsp + 20], r10d

    movzx r8d, BYTE PTR [rdx + 2]
    lea r9, [r8 + r8 * 2]
    shl r9, 2
    mov r10, QWORD PTR [rcx + 0x20 + r9]
    mov QWORD PTR [rsp + 24], r10
    mov r10d, DWORD PTR [rcx + 0x28 + r9]
    mov DWORD PTR [rsp + 32], r10d

    movzx r8d, BYTE PTR [rdx + 3]
    lea r9, [r8 + r8 * 2]
    shl r9, 2
    mov r10, QWORD PTR [rcx + 0x20 + r9]
    mov QWORD PTR [rsp + 36], r10
    mov r10d, DWORD PTR [rcx + 0x28 + r9]
    mov DWORD PTR [rsp + 44], r10d

    mov r10, QWORD PTR [rsp]
    mov QWORD PTR [rcx + 0x20], r10
    mov r10d, DWORD PTR [rsp + 8]
    mov DWORD PTR [rcx + 0x28], r10d
    mov r10, QWORD PTR [rsp + 12]
    mov QWORD PTR [rcx + 0x2C], r10
    mov r10d, DWORD PTR [rsp + 20]
    mov DWORD PTR [rcx + 0x34], r10d
    mov r10, QWORD PTR [rsp + 24]
    mov QWORD PTR [rcx + 0x38], r10
    mov r10d, DWORD PTR [rsp + 32]
    mov DWORD PTR [rcx + 0x40], r10d
    mov r10, QWORD PTR [rsp + 36]
    mov QWORD PTR [rcx + 0x44], r10
    mov r10d, DWORD PTR [rsp + 44]
    mov DWORD PTR [rcx + 0x4C], r10d

    add rsp, 56
    ret

.globl spc3_checksum_asm
spc3_checksum_asm:
    xor eax, eax
    lea r8, [rcx + 0x20]
    mov r9d, 24
.Lchecksum_loop:
    movzx edx, WORD PTR [r8]
    add eax, edx
    add r8, 2
    dec r9d
    jne .Lchecksum_loop
    and eax, 0xffff
    ret

.globl spc3_template_match_asm
spc3_template_match_asm:
    # Constant regions: OID..checksum-1, checksum+2..IV32-1, IV32+4..end.
    # Variable regions are PID, checksum, and IV32.
    mov r8, QWORD PTR [rcx + 0x04]
    cmp r8, QWORD PTR [rdx + 0x04]
    jne .Ltemplate_fail
    mov r8, QWORD PTR [rcx + 0x0C]
    cmp r8, QWORD PTR [rdx + 0x0C]
    jne .Ltemplate_fail
    mov r8, QWORD PTR [rcx + 0x14]
    cmp r8, QWORD PTR [rdx + 0x14]
    jne .Ltemplate_fail

    mov r8, QWORD PTR [rcx + 0x1E]
    cmp r8, QWORD PTR [rdx + 0x1E]
    jne .Ltemplate_fail
    mov r8, QWORD PTR [rcx + 0x26]
    cmp r8, QWORD PTR [rdx + 0x26]
    jne .Ltemplate_fail
    mov r8, QWORD PTR [rcx + 0x2E]
    cmp r8, QWORD PTR [rdx + 0x2E]
    jne .Ltemplate_fail
    mov r8, QWORD PTR [rcx + 0x36]
    cmp r8, QWORD PTR [rdx + 0x36]
    jne .Ltemplate_fail
    mov r8, QWORD PTR [rcx + 0x3E]
    cmp r8, QWORD PTR [rdx + 0x3E]
    jne .Ltemplate_fail
    movzx r8d, WORD PTR [rcx + 0x46]
    cmp r8w, WORD PTR [rdx + 0x46]
    jne .Ltemplate_fail

    mov r8d, DWORD PTR [rcx + 0x4C]
    cmp r8d, DWORD PTR [rdx + 0x4C]
    jne .Ltemplate_fail

    mov eax, 1
    ret

.Ltemplate_fail:
    xor eax, eax
    ret

.globl spc3_expand_iv32_exceptions_asm
spc3_expand_iv32_exceptions_asm:
    # Expand predictor IV32 plus bitmap/XOR exceptions for one full lane.
    # rcx = out u32[65536], rdx = predictor u32[65536],
    # r8 = bitmap[8192], r9 = XOR values u32[].
    mov r11, r9
    xor r10d, r10d
.Lexpand_iv32_loop:
    mov eax, DWORD PTR [rdx + r10 * 4]
    bt QWORD PTR [r8], r10
    jnc .Lexpand_iv32_store
    xor eax, DWORD PTR [r9]
    add r9, 4
.Lexpand_iv32_store:
    mov DWORD PTR [rcx + r10 * 4], eax
    inc r10
    cmp r10, 65536
    jne .Lexpand_iv32_loop
    mov rax, r9
    sub rax, r11
    shr rax, 2
    ret
