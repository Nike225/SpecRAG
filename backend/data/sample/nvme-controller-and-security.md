# NVMe Controller Initialization, Reset and Security (sample)

## 7 Controller Initialization and Shutdown

### 7.1 Controller initialization sequence

To bring up a PCIe NVMe controller the host: waits for CSTS.RDY to be 0 if the controller was previously enabled; programs the Admin Queue Attributes (AQA) register and the Admin Submission Queue (ASQ) and Admin Completion Queue (ACQ) base addresses; programs the Controller Configuration (CC) register with the command set (CSS), memory page size (MPS), arbitration mechanism (AMS) and I/O queue entry sizes (IOSQES = 6 for 64 bytes, IOCQES = 4 for 16 bytes); sets CC.EN to 1; and waits for CSTS.RDY to become 1.

The host then issues Identify to read the controller and namespace data structures, uses Set Features Number of Queues to request I/O queues, creates the I/O Completion Queues and then the I/O Submission Queues, and finally submits Asynchronous Event Request commands. If CSTS.RDY does not become 1 within the timeout reported in CAP.TO (in 500 millisecond units), the host should treat initialization as failed.

### 7.2 Controller reset

A Controller Level Reset is triggered by clearing CC.EN from 1 to 0, by an NVM Subsystem Reset, or by a PCIe conventional or function level reset. On a controller reset, all I/O queues are deleted, outstanding commands are aborted, and the controller registers other than the Admin Queue registers return to their default values. The host waits for CSTS.RDY to read 0 before re-enabling the controller. An NVM Subsystem Reset is initiated by writing 4E564D65h ("NVMe") to the NSSR register, when supported as reported by CAP.NSSRS.

### 7.3 Normal and abrupt shutdown

Before removing power, the host should request a shutdown by writing CC.SHN: 01b for a normal shutdown or 10b for an abrupt shutdown. The controller reports progress in CSTS.SHST: 01b shutdown processing occurring and 10b shutdown processing complete. For a normal shutdown, the host should first stop submitting I/O and delete the I/O queues. If power is removed without a shutdown notification, the Unsafe Shutdowns count in the SMART / Health Information log page is incremented.

## 9 Namespace Write Protection

Namespace write protection lets the host make a namespace read only. Support is reported in the Namespace Write Protection Capabilities (NWPC) field of Identify Controller: bit 0 indicates support for the No Write Protect and Write Protect states, bit 1 for Write Protect Until Power Cycle, and bit 2 for Permanent Write Protect.

### 9.1 Write protection states

The state is controlled with Set Features using the Namespace Write Protection Config feature (FID 84h). The Write Protection State (WPS) is Command Dword 11 bits 02:00: 000b No Write Protect, 001b Write Protect, 010b Write Protect Until Power Cycle, 100b Permanent Write Protect. The NSID shall identify a single namespace; the broadcast value FFFFFFFFh is not allowed and shall result in status Invalid Field in Command.

A namespace in Write Protect Until Power Cycle returns to No Write Protect after the next power cycle. Permanent Write Protect is irreversible: once entered, the state cannot be changed by any command or by a power cycle, and an attempt to change it shall be aborted.

### 9.2 Commands affected by write protection

While a namespace is write protected, commands that modify user data or metadata in it shall be aborted with status Namespace is Write Protected. This includes Write, Write Zeroes, Write Uncorrectable, Dataset Management with the deallocate attribute, and Format NVM. Read, Compare, Verify and Flush continue to operate normally. A Sanitize operation shall not be started while any namespace in the NVM subsystem is write protected.

## 10 Security Send and Security Receive

### 10.1 Command fields

Security Send (opcode 81h) transfers security protocol data to the controller and Security Receive (opcode 82h) retrieves it. Both use the Security Protocol (SECP) field in Command Dword 10 bits 31:24, the SP Specific field (SPSP) in bits 23:08 and NVMe Security Specific Field (NSSF) in bits 07:00. Command Dword 11 holds the Transfer Length (TL) for Security Send or the Allocation Length (AL) for Security Receive.

### 10.2 Security protocol discovery

Security Receive with SECP 00h returns the list of security protocols the controller supports, such as the TCG protocols used by self-encrypting drives (Opal) and the NVMe protocol (EAh) used for Replay Protected Memory Block access. If the host requests a protocol that is not supported, the controller shall abort the command with status Invalid Field in Command.

### 10.3 Locking and command restriction

When a TCG locking range is locked, read and write commands to LBAs in that range shall fail with status Access Denied (a Media and Data Integrity Error). Locking state survives a controller reset but, depending on the locking policy, may be reset to locked by a power cycle. Validation should cover read and write access to locked and unlocked ranges, boundary LBAs at the edges of a range, and the behavior after a power cycle.
