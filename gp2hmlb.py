#!/usr/bin/env python3

'''
Author  : Andreas Puschendorf, DL7OAP
Version : 007
Date    : 2019-12-12

This script is plugged between gpredict and hamlib based on python 3.7
It is listing on port 4532 for gpredict frequencies
and it is sending frequencies and startsequences for ic9700, ic9100 and ic910 to port 4572 for hamlib

Usage: python3 gp2hmlb.py [type_of_satellite] [band_of_uplink]
gp2hmlb.py hast to be called with 2 parameter
first parameter is type of satellite. you can choose between FM, SSB, USB, SIMPLEX, CW
second parameter is type of uplink. You can choose between 2M, 70CM, 23CM

1t step:    starting hamlib daemon on port 4572
    Linux:      rigctld -m 381 -r /dev/ic9700a -s 19200 -t 4572
    Windows:    rigctld -m 381 -r COM5 -s 19200 -t 4572
2t step:    starting gp2hmlb.py pythonscript
    Linux:      python3 gp2hmlb.py FM 70CM
    Windows:    python3 gp2hmlb.py FM 70CM
3t step:    start gpredict with a duplex trx on port 4532 and MAIN/SUB
'''


import socket
import sys

HOST = '127.0.0.1'  # Standard loopback interface address (localhost)
PORT_SERVER = 4532  # Port to listen on (non-privileged ports are > 1023)
PORT_CLIENT = 4572  # Port to listen on (non-privileged ports are > 1023)
FREQUENCY_OFFSET = 25  # needed Frequency shift in Hz before a correction is send to transceiver


def sendCommandToHamlib(sock_hamlib, command):
    b_cmd = bytearray()
    b_cmd.extend(map(ord, command + '\n'))
    sock_hamlib.send(b_cmd)
    waiting_for_answer = True
    return_value = ''
    while waiting_for_answer:
        return_value = sock_hamlib.recv(100).decode('utf-8')
        if len(return_value) > 0:
            waiting_for_answer = False

    if 'RPRT -' in return_value:
        print('hamlib: ' + return_value.replace('\n', '') + ' for command ' + command)
    return return_value


def setStartSequenceGeneral(sock_hamlib, type_of_satellite):

    # define uplink
    sendCommandToHamlib(sock_hamlib, 'V Main')
    sendCommandToHamlib(sock_hamlib, 'L SQL 0')
    sendCommandToHamlib(sock_hamlib, 'L AF 0')
    sendCommandToHamlib(sock_hamlib, 'R None')

    # define downlink
    sendCommandToHamlib(sock_hamlib, 'V Sub')
    sendCommandToHamlib(sock_hamlib, 'L SQL 0')
    sendCommandToHamlib(sock_hamlib, 'L AF 0.09')
    sendCommandToHamlib(sock_hamlib, 'R None')

    if type_of_satellite == 'SIMPLEX':
        sendCommandToHamlib(sock_hamlib, 'V Main')


def setStartSequenceSSB(sock_hamlib, usb=False):

    # define uplink
    sendCommandToHamlib(sock_hamlib, 'V Main')
    sendCommandToHamlib(sock_hamlib, 'V VFOA')
    if usb: sendCommandToHamlib(sock_hamlib, 'M USB 2400')
    else: sendCommandToHamlib(sock_hamlib, 'M LSB 2400')
    sendCommandToHamlib(sock_hamlib, 'S 0 Main')

    # define downlink
    sendCommandToHamlib(sock_hamlib, 'V Sub')
    sendCommandToHamlib(sock_hamlib, 'V VFOA')
    if usb: sendCommandToHamlib(sock_hamlib, 'M LSB 2400')
    else: sendCommandToHamlib(sock_hamlib, 'M USB 2400')


def setStartSequenceCW(sock_hamlib):

    # define uplink
    sendCommandToHamlib(sock_hamlib, 'V Main')
    sendCommandToHamlib(sock_hamlib, 'V VFOA')
    sendCommandToHamlib(sock_hamlib, 'M CW 500')
    sendCommandToHamlib(sock_hamlib, 'S 0 Main')

    # define downlink
    sendCommandToHamlib(sock_hamlib, 'V Sub')
    sendCommandToHamlib(sock_hamlib, 'V VFOA')
    sendCommandToHamlib(sock_hamlib, 'M USB 2400')


def setStartSequenceFM(sock_hamlib):

    # define uplink
    sendCommandToHamlib(sock_hamlib, 'V Main')
    sendCommandToHamlib(sock_hamlib, 'V VFOA')
    sendCommandToHamlib(sock_hamlib, 'M FM 15000')
    sendCommandToHamlib(sock_hamlib, 'S 0 Main')
    sendCommandToHamlib(sock_hamlib, 'U AFC 0')
    sendCommandToHamlib(sock_hamlib, 'J 0')
    sendCommandToHamlib(sock_hamlib, 'U RIT 0')

    sendCommandToHamlib(sock_hamlib, 'C 670')
    sendCommandToHamlib(sock_hamlib, 'U TONE 1')

    # define downlink
    sendCommandToHamlib(sock_hamlib, 'V Sub')
    sendCommandToHamlib(sock_hamlib, 'V VFOA')
    sendCommandToHamlib(sock_hamlib, 'M FM 15000')
    sendCommandToHamlib(sock_hamlib, 'U TONE 0')
    sendCommandToHamlib(sock_hamlib, 'U AFC 1')
    sendCommandToHamlib(sock_hamlib, 'J 0')
    sendCommandToHamlib(sock_hamlib, 'U RIT 0')


def setStartSequenceSimplex(sock_hamlib):

    # define uplink
    sendCommandToHamlib(sock_hamlib, 'V Main')
    sendCommandToHamlib(sock_hamlib, 'V VFOB')
    sendCommandToHamlib(sock_hamlib, 'M FM 15000')
    sendCommandToHamlib(sock_hamlib, 'S 0 Main')
    sendCommandToHamlib(sock_hamlib, 'U AFC 0')
    sendCommandToHamlib(sock_hamlib, 'J 0')
    sendCommandToHamlib(sock_hamlib, 'U RIT 0')

    # define downlink
    sendCommandToHamlib(sock_hamlib, 'V VFOA')
    sendCommandToHamlib(sock_hamlib, 'M FM 15000')
    sendCommandToHamlib(sock_hamlib, 'U TONE 0')
    sendCommandToHamlib(sock_hamlib, 'S 1 Main')
    sendCommandToHamlib(sock_hamlib, 'U AFC 1')
    sendCommandToHamlib(sock_hamlib, 'J 0')
    sendCommandToHamlib(sock_hamlib, 'U RIT 0')


def loopSSBandFMandCW(sock_hamlib, up, dw):

    # set uplink frequency
    sendCommandToHamlib(sock_hamlib, 'V Main')
    sendCommandToHamlib(sock_hamlib, 'F ' + up)

    # set downlink frequency
    sendCommandToHamlib(sock_hamlib, 'V Sub')
    sendCommandToHamlib(sock_hamlib, 'F ' + dw)


def loopSIMPLEX(sock_hamlib, up, dw):

    # only update of frequencies if PTT off
    if sendCommandToHamlib(sock_hamlib, 't')[0] == '0':

        # set uplink frequency
        sendCommandToHamlib(sock_hamlib, 'V VFOB')
        sendCommandToHamlib(sock_hamlib, 'F ' + up )

        # set downlink frequency
        sendCommandToHamlib(sock_hamlib, 'V VFOA')
        sendCommandToHamlib(sock_hamlib, 'F ' + dw)


def getBandFromFrequency(frequency):
    band = '2M'
    if int(frequency) > 150000000:
        band = '70CM'
    if int(frequency) > 470000000:
        band = '23CM'
    return band


def activateCorrectUplinkBandInMain(sock_hamlib, type_of_uplink_band):
    sendCommandToHamlib(sock_hamlib, 'V Main')
    main_frequency = sendCommandToHamlib(sock_hamlib, 'f')
    sendCommandToHamlib(sock_hamlib, 'V Sub')
    sub_frequency = sendCommandToHamlib(sock_hamlib, 'f')

    if type_of_uplink_band == getBandFromFrequency(main_frequency):  # is uplink band in main -> nothing to do
        return
    elif type_of_uplink_band == getBandFromFrequency(sub_frequency):  # is uplink band in sub -> switch bands
        sendCommandToHamlib(sock_hamlib, 'G XCHG')
    else:  # is uplink band not in main and sub -> set band in main
        sendCommandToHamlib(sock_hamlib, 'V Main')
        if type_of_uplink_band == '23CM':
            sendCommandToHamlib(sock_hamlib, 'F 1295000000')
        if type_of_uplink_band == '70CM':
            sendCommandToHamlib(sock_hamlib, 'F 435000000')
        if type_of_uplink_band == '2M':
            sendCommandToHamlib(sock_hamlib, 'F 145900000')


def main():

    uplink = '0'
    downlink = '0'
    last_uplink = '0'
    last_downlink = '0'

    ###############################################
    # start sockets for gpredict und hamlib
    ###############################################

    # start tcp client
    try:
        sock_hamlib = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock_hamlib.connect((HOST, PORT_CLIENT))
    except socket.error as e:
        print('Problem: ', e)
        print('INFO: connection refused error is most of the time that rigctld is not started on port 4572')
        print('use command "rigctl -m 381 -r /dev/ic9700a -s 19200 -t 4572"')

    # start tcp server
    sock_gpredict = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_gpredict.bind((HOST, PORT_SERVER))
    sock_gpredict.listen(1)

    ###############################################
    # satellite start sequence
    ###############################################

    if (len(sys.argv) == 3):
        type_of_satellite = sys.argv[1].upper()
        type_of_uplink_band = sys.argv[2].upper()

        if type_of_uplink_band not in ['70CM', '2M', '23CM']:
            print('ERROR: unknown uplink band type "' + type_of_uplink_band + '". Only 2M, 70CM and 23CM possible.')
            return

        activateCorrectUplinkBandInMain(sock_hamlib, type_of_uplink_band)

        if type_of_satellite == 'SSB':
            setStartSequenceSSB(sock_hamlib)
        elif type_of_satellite == 'USB':
            setStartSequenceSSB(sock_hamlib, True)
        elif type_of_satellite == 'CW':
            setStartSequenceCW(sock_hamlib)
        elif type_of_satellite == 'FM':
            setStartSequenceFM(sock_hamlib)
        elif type_of_satellite == 'SIMPLEX':
            setStartSequenceSimplex(sock_hamlib)
        else:
            print('ERROR: unkown satellite type "' + type_of_satellite
                  + '". Only SSB, USB, FM, SIMPLEX or CW possible.')
            return
        setStartSequenceGeneral(sock_hamlib, type_of_satellite)

    else:
        print('Usage: gp2hmlb.py [type_of_satellite] [band_of_uplink]')
        print('INFO: gp2hmlb.py hast to be called with 2 parameter')
        print('first parameter is type of satellite. you can choose between FM, SSB, USB, SIMPLEX, CW')
        print('second parameter is type of Uplink. you can choose between 2M, 70CM, 23CM')
        print('example: python3 gp2hmlb.py SSB 2M')
        return

    ###############################################
    # main loop
    ###############################################

    while True:
        conn, addr = sock_gpredict.accept()
        print('Connected by', addr)
        while 1:
            data = conn.recv(1000)
            print('gpredict: ' + data.decode('utf-8').replace('\n', ''))
            if not data: break
            if data[0] in [70, 73]:  # I, F
                # get downlink and uplink from gpredict
                cut = data.decode('utf-8').split(' ')
                if data[0] == 70:  # F - gpredict ask for downlink
                    downlink = cut[len(cut)-1].replace('\n', '')
                if data[0] == 73:  # I - gpredict ask for uplink
                    uplink = cut[len(cut)-1].replace('\n', '')
                print('gp2hmlb: last  ^ ' + last_uplink + ' v ' + last_downlink)
                print('gp2hmlb: fresh ^ ' + uplink + ' v ' + downlink)
                # only if uplink or downlink changed > 0 10Hz Column, then update
                if (abs(int(last_uplink)-int(uplink)) > FREQUENCY_OFFSET) \
                        or (abs(int(last_downlink)-int(downlink)) > FREQUENCY_OFFSET):
                    if type_of_satellite in ['SSB', 'USB', 'CW', 'FM']:
                        loopSSBandFMandCW(sock_hamlib, uplink, downlink)
                    if type_of_satellite == 'SIMPLEX':
                        loopSIMPLEX(sock_hamlib, uplink, downlink)
                    last_uplink = uplink
                    last_downlink = downlink
                conn.send(b'RPRT 0')  # Return Data OK to gpredict
            elif data[0] in [102, 105]:
                if type_of_satellite in ['SIMPLEX']:
                    conn.send(b'RPRT')
                else:
                    if data[0] == 102:  # f - gpredict ask for downlink
                        actual_sub_frequency = sendCommandToHamlib(sock_hamlib, 'f').replace('\n', '')
                        if actual_sub_frequency.find('RPRT') == -1:
                            downlink = actual_sub_frequency
                            last_downlink = actual_sub_frequency
                            print('gp2hmlb: dial down: ' + actual_sub_frequency)
                            b = bytearray()
                            b.extend(map(ord, actual_sub_frequency + '\n'))
                            conn.send(b)
                    elif data[0] == 105:  # i - gpredict ask for uplink
                        # we do not look for dial on uplink,
                        # we just ignore it and send back the last uplink frequency
                        b = bytearray()
                        b.extend(map(ord, uplink + '\n'))
                        conn.send(b)
            elif data[0] == 116:  # t ptt
                conn.send(b'0')
            else:
                conn.send(b'RPRT 0')  # Return Data OK to gpredict
        print('connect closed')
        conn.close()


main()
