## original script T. Terasawa, ICRR, Dec 2022
## Updated by H. Yokokawa, Feb 2023
## Last modified by S. Asayama, SKAO, 03 Mar 2023


using Mmap, Printf, FFTW, Dates, HDF5, Statistics , StatsBase

function dobunkai(fname)
    expr="/"
    if(occursin.(expr,fname))
       bunkai=split(fname,expr)
    else
       bunkai=[]
       push!(bunkai, fname)
    end
   return bunkai
end

function  ChangeFileExt(fname, newext)
   bunkai=dobunkai(fname)
   nmax=length(bunkai)
   slast=bunkai[nmax]
   mmax=length(slast)
   mfound=0
   for m=mmax:-1:1
      if(slast[m]=='.')
         mfound=m
         break
      end
   end

   newname=""
   for n=1:nmax-1
      newname*='/'*bunkai[n]
   end

   if(mfound<=1)
      newname*=slast
   else
      newname*=slast[1:mfound-1]
   end

   return newname*newext
end


function open_rawdata(fname)
    try
      IN=open(fname,"r")
      @time raw=Mmap.mmap(IN, Vector{ComplexF32})  #ファイル全体を配列のイメージgqrxにマップしておく（ここでは仮想的読み込み）
      generalNmax=sizeof(raw)÷64
      close(IN)
      println("FileOpenOK: $fname")
      return generalNmax, raw
    catch
      println("FileOpenError: Not Found $fname")
    finally
    end
  end

@inline function takeABS2(c::ComplexF32);    return abs(c)^2; end

function FileLister(expr)
  filelist=readdir()
  np=length(filelist)
  result=[]
  for n=1:np
    if(occursin.(expr,filelist[n]))
      push!(result, filelist[n])
    end
  end
  return result
end


if ARGS != []
  n = length(ARGS)
  println("Proceed ", ARGS)
else
  println("Please enter Filename Duration and sumpletime \n\n")
  exit(1)
end

myfname=ARGS[1]
@printf("myfname=%s", myfname)
Nmax, gqrx=open_rawdata(myfname)

samplingRate=parse(Int,split(myfname,"_")[5])
fcenter=parse(Int,split(myfname,"_")[4])/1e6

@printf("Data duration=%s[s]\n", length(gqrx)÷samplingRate)

if length(gqrx)÷samplingRate > 10800
  duration = 10800                              # duration sec
else
  duration = length(gqrx)÷samplingRate         # duration sec
end

@printf("Reducing: %s[s]\n", duration)

avesec = 0.001                         # integration time
@printf("Data sample =%.4f[s]\n", avesec)



samplingINms =samplingRate÷1000

nData  =Int64(floor(samplingRate*avesec))  # avesec秒分のデータをとりだす
m100   =100                  # m100点のパワースペクトルを計算(作図の都合で800に固定）
Nave   =Int64(floor(nData÷m100))          # ÷は整数の割り算, Naveはスペクトルを計算する際の平均個数（自由度）
Mfmax  =m100 ÷2             #


Nsample = Int64(floor(duration/avesec))
PWRM = zeros(Float32,(m100,Nsample))
zero_array = fill(0, m100, 1)

#################################################################################
for Nd in 1:Nsample
  if (Int64((Nd-1) % (1/avesec*60)) == 0)
    @printf("%d min: %s \n",  Nd/(1/avesec*60), now())
    prt=1
  end

  pwrM =zeros(Float32, m100)   # 配列準備（平均化のため）

  n1= (Nd-1)*nData +1
  n2= n1 + nData -1
  mydata=gqrx[n1:n2]       # 1～nDataのデータを実際に読み出す
  fdata =fftshift(fft(mydata))         # fft
  pwrN=@. takeABS2(fdata)    # vector化命令で全体のパワースペクトルを一挙に計算
  part=reshape(pwrN, Nave, m100)　#Nave個ごとの平均操作のため、1次元配列pwrN[1:nData]を2次元配列part[1:Nave, 1:m100]に並べ替え

  for m=1:m100                               # positive freq (m=Mfmax+1からm100)
    pwrM[ m]=sum(@view part[:,m])/Nave       # Nave個ごとに足し合わせる。[:]は和が1-Nave全体にわたることを指示
  end

  if isinf(mean(pwrM))
    PWRM[:,Nd] = zero_array
    @printf("Inf @ %s[s]\n", Nd)
  elseif isnan(mean(pwrM))
    PWRM[:,Nd] = zero_array
    @printf("NaN @ %s[s]\n", Nd)
  else
    PWRM[:,Nd] = pwrM
  end

end
#-----
gqrx = nothing
zero_array = nothing
mydata = nothing
fdata = nothing
pwrN = nothing
pwrM = nothing
part = nothing
GC.gc()

#----
#
@printf("Calc. Para\n")
@printf("ALL std %f \n",std(PWRM))
@printf("ALL mean %f \n",mean(PWRM))
@printf("All Max=%.10f\n", maximum(PWRM))

#----
trim_PWRM = trim(vec(PWRM), prop=0.2)
@printf("Passed Trim\n")

trim_std = std(trim_PWRM)
trim_mean = mean(trim_PWRM)
trim_max = maximum(trim_PWRM)

trim_PWRM=nothing
GC.gc()

@printf("Trim std %.5f\n",trim_std)
@printf("Trim mean %.5f\n",trim_mean)
@printf("Trim STD=%.10f\n", trim_std)
@printf("Trim Max=%.10f\n", trim_max)

PWRM = PWRM./trim_std
@printf("Passed PWRM std\n")

PWRM[findall(PWRM.>255)] .= 0; PWRM
@printf("Passed findall \n")
@printf("Norm ave:%.5f\n",mean(PWRM))
#---


PWRM8 = round.(UInt8,PWRM)
@printf("PWRM converted UINT8\n")

PWRM=nothing
GC.gc()

s=ChangeFileExt(myfname,"")
hdf5_name = s * "_d" * string(Int64(floor(duration))) * "s_sp" * string(Int64(floor(avesec*1e3))) *"ms_ch" * string(m100)
@printf("hdf5 name:%s\n",hdf5_name)
h5open(hdf5_name * ".hdf5", "w") do file
    file[hdf5_name]=PWRM8
end
